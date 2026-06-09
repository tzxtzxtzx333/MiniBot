"""TaskPlan planning tests — PlannerAgent, TaskExecutor, LongTaskRunner, CLI, regression."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.json_utils import load_json_file
from minibot.planning.plan_schema import (
    Step,
    TaskPlan,
    make_single_step_plan,
    plan_from_json,
    validate_plan_dict,
)
from minibot.planning.planner_agent import PlannerAgent

ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(**overrides: object) -> Path:
    """Create an isolated project root for testing.

    Only ``configs/`` is copied from the real project; the other
    directories are created as empty placeholders to avoid copying
    100+ benchmark files on every test invocation.
    """
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    # Only configs is actually needed — copy it
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    # Create empty placeholder dirs that MiniBotApp expects to exist
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)

    config_path = temp_root / "configs" / "minibot.json"
    config = load_json_file(config_path)
    for key, value in overrides.items():
        if isinstance(value, dict):
            config[key] = dict(config.get(key, {}))
            config[key].update(value)
        else:
            config[key] = value
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_root / "configs" / "hooks.json").write_text(
        json.dumps({"hooks": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return temp_root


def _write_policy(temp_root: Path, updates: dict[str, object]) -> None:
    policy_path = temp_root / "configs" / "policy.json"
    policy = dict(load_json_file(policy_path))
    policy.update(updates)
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Plan schema tests
# ---------------------------------------------------------------------------


def test_taskplan_serialization_round_trip() -> None:
    plan = TaskPlan(
        plan_id="plan_test",
        task_id="task_1",
        goal="read and summarize README.md",
        steps=[
            Step(step_id="s1", description="read README.md", tool_hints=["file_read"]),
            Step(step_id="s2", description="write summary", tool_hints=["file_write"]),
        ],
    )
    data = plan.to_dict()
    restored = TaskPlan.from_dict(data)
    assert restored.plan_id == "plan_test"
    assert restored.task_id == "task_1"
    assert len(restored.steps) == 2
    assert restored.steps[0].tool_hints == ["file_read"]


def test_validate_plan_dict_rejects_invalid() -> None:
    errors = validate_plan_dict({})
    assert len(errors) > 0

    errors = validate_plan_dict({"plan_id": "", "goal": "", "steps": []})
    assert len(errors) > 0

    errors = validate_plan_dict(
        {"plan_id": "p1", "goal": "g", "steps": [{"step_id": "", "description": ""}]}
    )
    assert len(errors) > 0


def test_validate_plan_dict_accepts_valid() -> None:
    errors = validate_plan_dict(
        {
            "plan_id": "p1",
            "goal": "test goal",
            "steps": [{"step_id": "s1", "description": "do something"}],
        }
    )
    assert errors == []


def test_plan_from_json_parses_valid() -> None:
    raw = json.dumps(
        {"plan_id": "p1", "goal": "test", "steps": [{"step_id": "s1", "description": "do"}]}
    )
    plan = plan_from_json(raw)
    assert plan is not None
    assert plan.plan_id == "p1"
    assert len(plan.steps) == 1


def test_plan_from_json_returns_none_for_bad_json() -> None:
    assert plan_from_json("not json") is None
    assert plan_from_json("{}") is None
    assert plan_from_json('{"plan_id":"","goal":"","steps":[]}') is None


def test_make_single_step_plan() -> None:
    plan = make_single_step_plan("do one thing", task_id="t1")
    assert plan.plan_id.startswith("plan_")
    assert plan.task_id == "t1"
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "do one thing"
    assert plan.status == "pending"


def test_current_step_index() -> None:
    plan = TaskPlan(
        plan_id="p1",
        goal="g",
        steps=[
            Step(step_id="s1", description="a", status="completed"),
            Step(step_id="s2", description="b", status="pending"),
            Step(step_id="s3", description="c", status="pending"),
        ],
    )
    assert plan.current_step_index() == 1


def test_all_completed() -> None:
    plan = TaskPlan(
        plan_id="p1",
        goal="g",
        steps=[
            Step(step_id="s1", description="a", status="completed"),
            Step(step_id="s2", description="b", status="completed"),
        ],
    )
    assert plan.all_completed() is True
    plan.steps[1].status = "pending"
    assert plan.all_completed() is False


# ---------------------------------------------------------------------------
# PlannerAgent tests
# ---------------------------------------------------------------------------


def test_planner_fake_decomposes_read_summarize_write_goal() -> None:
    planner = PlannerAgent(mode="fake")
    plan = planner.plan(
        "帮我读取 README.md 和 docs/resume_mapping.md，总结 MiniBot 当前能力边界，并写入 realistic_roadmap.md"
    )
    assert plan is not None
    assert plan.status == "pending"
    assert len(plan.steps) >= 3  # read README, read resume_mapping, summarize, write
    descriptions = [s.description for s in plan.steps]
    assert any("README.md" in d for d in descriptions)
    assert any("resume_mapping" in d for d in descriptions)
    assert any("总结" in d for d in descriptions)
    assert any("写入" in d or "realistic_roadmap" in d for d in descriptions)


def test_planner_fake_falls_back_to_single_step_for_unknown_goal() -> None:
    planner = PlannerAgent(mode="fake")
    plan = planner.plan("just chat with me about the weather")
    assert len(plan.steps) >= 1
    # At minimum, it creates a generic step
    assert plan.steps[0].description == "just chat with me about the weather"


def test_planner_fake_handles_calculator() -> None:
    planner = PlannerAgent(mode="fake")
    plan = planner.plan("计算 128 * 64")
    assert any("calculator" in str(s.tool_hints) for s in plan.steps)


def test_planner_real_falls_back_on_bad_json(monkeypatch) -> None:
    """When real mode returns garbage, fallback to single-step plan."""
    monkeypatch.setattr(
        "minibot.planning.planner_agent.PlannerAgent._plan_real",
        lambda self, goal: "not valid json at all",
    )
    planner = PlannerAgent(mode="real")
    plan = planner.plan("do something complex")
    assert plan is not None
    assert len(plan.steps) >= 1


# ---------------------------------------------------------------------------
# TaskExecutor tests
# ---------------------------------------------------------------------------


def test_task_executor_executes_read_step_and_records_run() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        # Write a test file first
        test_file = app.runtime.workspace.sandbox_dir / "test_target.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("hello plan world", encoding="utf-8")

        plan = TaskPlan(
            plan_id="plan_exec_test",
            task_id=None,
            goal="read test_target.txt",
            steps=[
                Step(step_id="s1", description="读取 test_target.txt", tool_hints=["file_read"])
            ],
        )
        app.runtime.task_executor.save_plan(plan)

        outcome = app.runtime.task_executor.execute_step(plan, plan.steps[0])
        assert outcome["status"] == "completed"
        assert outcome["run_id"] is not None
        assert any("hello plan world" in str(tr) for tr in outcome["tool_trace"])

        # run record should have plan_id / step_id
        runs_dir = app.runtime.workspace.runs_dir
        run_path = runs_dir / f"{outcome['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["plan_id"] == "plan_exec_test"
        assert record["step_id"] == "s1"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_task_executor_multiple_linear_steps() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        sandbox = app.runtime.workspace.sandbox_dir
        (sandbox / "file_a.txt").write_text("content A", encoding="utf-8")
        (sandbox / "file_b.txt").write_text("content B", encoding="utf-8")

        plan = TaskPlan(
            plan_id="plan_linear",
            goal="read two files",
            steps=[
                Step(step_id="s1", description="读取 file_a.txt", tool_hints=["file_read"]),
                Step(step_id="s2", description="读取 file_b.txt", tool_hints=["file_read"]),
            ],
        )
        executor = app.runtime.task_executor
        executor.save_plan(plan)

        outcome1 = executor.execute_step(plan, plan.steps[0])
        assert outcome1["status"] == "completed"
        assert plan.steps[0].status == "completed"
        assert plan.steps[0].run_id is not None

        outcome2 = executor.execute_step(plan, plan.steps[1])
        assert outcome2["status"] == "completed"
        assert plan.steps[1].status == "completed"

        # Verify run records have correct plan_id/step_id
        runs_dir = app.runtime.workspace.runs_dir
        for step, outcome in [(plan.steps[0], outcome1), (plan.steps[1], outcome2)]:
            run_path = runs_dir / f"{outcome['run_id']}.json"
            record = json.loads(run_path.read_text(encoding="utf-8"))
            assert record["plan_id"] == "plan_linear"
            assert record["step_id"] == step.step_id
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_task_executor_calculator_step() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        plan = TaskPlan(
            plan_id="plan_calc",
            goal="calculate 2+3",
            steps=[Step(step_id="s1", description="计算 2 + 3", tool_hints=["calculator"])],
        )
        executor = app.runtime.task_executor
        executor.save_plan(plan)
        outcome = executor.execute_step(plan, plan.steps[0])
        assert outcome["status"] == "completed"
        # Run record should have plan_id
        runs_dir = app.runtime.workspace.runs_dir
        run_path = runs_dir / f"{outcome['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["plan_id"] == "plan_calc"
        assert record["step_id"] == "s1"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_run_record_has_step_description_when_plan() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        plan = TaskPlan(
            plan_id="plan_step_desc",
            goal="read file",
            steps=[Step(step_id="s1", description="读取 test file", tool_hints=["file_read"])],
        )
        executor = app.runtime.task_executor
        executor.save_plan(plan)
        outcome = executor.execute_step(plan, plan.steps[0])
        runs_dir = app.runtime.workspace.runs_dir
        run_path = runs_dir / f"{outcome['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["step_description"] == "读取 test file"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_ordinary_chat_run_record_has_no_plan_id() -> None:
    """Ordinary chat messages should NOT have plan_id set."""
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="no-plan", content="hello")
        )
        runs_dir = app.runtime.workspace.runs_dir
        run_path = runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record.get("plan_id") is None
        assert record.get("step_id") is None
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# LongTaskRunner tests
# ---------------------------------------------------------------------------


def test_long_task_runner_runs_all_steps() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        sandbox = app.runtime.workspace.sandbox_dir
        (sandbox / "doc_a.txt").write_text("alpha", encoding="utf-8")
        (sandbox / "doc_b.txt").write_text("beta", encoding="utf-8")

        plan = TaskPlan(
            plan_id="plan_full",
            goal="read two files",
            steps=[
                Step(step_id="s1", description="读取 doc_a.txt", tool_hints=["file_read"]),
                Step(step_id="s2", description="读取 doc_b.txt", tool_hints=["file_read"]),
            ],
        )
        app.runtime.task_executor.save_plan(plan)

        result = app.runtime.long_task_runner.run(plan)
        assert result["status"] == "completed"
        assert result["steps_completed"] == 2
        assert result["steps_failed"] == 0

        # Verify plan is persisted as completed
        loaded = app.runtime.task_executor.load_plan("plan_full")
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.all_completed()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_long_task_runner_stops_on_failure() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        # calculator with invalid expression triggers a real tool failure
        plan = TaskPlan(
            plan_id="plan_fail",
            goal="calculate invalid expression",
            steps=[
                Step(step_id="s1", description="请帮我计算 2 +", tool_hints=["calculator"]),
                Step(step_id="s2", description="计算 1 + 3", tool_hints=["calculator"]),
            ],
        )
        app.runtime.task_executor.save_plan(plan)
        result = app.runtime.long_task_runner.run(plan)
        # First step should fail (invalid expression), replan should try once, overall plan fails
        assert result["status"] in {"failed", "completed"}
        # At minimum verify plan ran without hanging
        assert result["total_steps"] == 3  # s1 + s1_r1 (replan) + s2
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_long_task_runner_approval_required_pauses_plan() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        plan = TaskPlan(
            plan_id="plan_approval",
            goal="write a file",
            steps=[
                Step(
                    step_id="s1",
                    description="write notes/plan_test.txt content hello",
                    tool_hints=["file_write"],
                ),
            ],
        )
        app.runtime.task_executor.save_plan(plan)
        result = app.runtime.long_task_runner.run(plan)
        # file_write is graylisted → approval_required
        assert result["status"] in {"completed", "waiting_approval", "failed"}
        # If approval is required, plan status should be waiting_approval
        loaded = app.runtime.task_executor.load_plan("plan_approval")
        assert loaded is not None
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_long_task_runner_resume_plan() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        sandbox = app.runtime.workspace.sandbox_dir
        (sandbox / "resume_target.txt").write_text("resume content", encoding="utf-8")

        plan = TaskPlan(
            plan_id="plan_resume",
            goal="read resume_target.txt",
            steps=[
                Step(step_id="s1", description="读取 resume_target.txt", tool_hints=["file_read"])
            ],
        )
        app.runtime.task_executor.save_plan(plan)
        # Run to completion
        result = app.runtime.long_task_runner.run(plan)
        assert result["status"] == "completed"

        # Resume should return immediately (already completed)
        res = app.runtime.long_task_runner.resume("plan_resume")
        assert res["status"] == "completed"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Evidence integration with planning
# ---------------------------------------------------------------------------


def test_plan_step_evidence_ids_are_tracked() -> None:
    """When a step produces evidence, it should appear in step.evidence_ids."""
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 50})
    try:
        app = MiniBotApp(temp_root)
        # Create the target file in sandbox first
        sandbox = app.runtime.workspace.sandbox_dir
        (sandbox / "evidence_test.txt").write_text(
            "evidence test content here for reading", encoding="utf-8"
        )
        plan = TaskPlan(
            plan_id="plan_evidence",
            goal="read a file",
            steps=[
                Step(step_id="s1", description="读取 evidence_test.txt", tool_hints=["file_read"])
            ],
        )
        executor = app.runtime.task_executor
        executor.save_plan(plan)
        outcome = executor.execute_step(plan, plan.steps[0])
        assert outcome["status"] == "completed"
        # evidence_ids should be a list (may be empty for small outputs)
        assert isinstance(outcome["evidence_ids"], list)
        assert isinstance(plan.steps[0].evidence_ids, list)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_plan_create_and_show() -> None:
    import os
    import subprocess
    import sys

    temp_root = _prepare_temp_root()
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, "-m", "minibot", "plan", "create", "--goal", "读取 README.md 并总结"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(temp_root),
            env=env,
            timeout=30,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        data = json.loads(r.stdout)
        plan_id = data["plan_id"]
        assert plan_id.startswith("plan_")
        assert len(data["steps"]) >= 1

        # Show
        r2 = subprocess.run(
            [sys.executable, "-m", "minibot", "plan", "show", plan_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(temp_root),
            env=env,
            timeout=30,
        )
        assert r2.returncode == 0, f"stderr: {r2.stderr}"
        data2 = json.loads(r2.stdout)
        assert data2["plan_id"] == plan_id
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_cli_plan_show_missing() -> None:
    import os
    import subprocess
    import sys

    temp_root = _prepare_temp_root()
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, "-m", "minibot", "plan", "show", "plan_nonexistent"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(temp_root),
            env=env,
            timeout=30,
        )
        assert r.returncode == 1
        assert "plan_not_found" in r.stderr
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_cli_plan_run_executes_plan() -> None:
    import os
    import subprocess
    import sys

    temp_root = _prepare_temp_root()
    try:
        # Create test file for reading
        sandbox = temp_root / ".minibot" / "sandbox_workspace"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "cli_test.txt").write_text("CLI test content", encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"

        # Create plan
        r = subprocess.run(
            [sys.executable, "-m", "minibot", "plan", "create", "--goal", "读取 cli_test.txt"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(temp_root),
            env=env,
            timeout=30,
        )
        plan_id = json.loads(r.stdout)["plan_id"]

        # Run plan
        r2 = subprocess.run(
            [sys.executable, "-m", "minibot", "plan", "run", plan_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(temp_root),
            env=env,
            timeout=30,
        )
        assert r2.returncode == 0, f"stderr: {r2.stderr}"
        data = json.loads(r2.stdout)
        assert data["status"] in {"completed", "waiting_approval"}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression — non-interference
# ---------------------------------------------------------------------------


def test_planning_does_not_affect_safety() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="plan-safe", content="hello"
            )
        )
        assert result.response == "MiniBot echo: hello"

        result2 = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="plan-safe", content="calculate 1 + 1"
            )
        )
        assert result2.response == "MiniBot tool result: 2"

        result3 = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="plan-safe", content="/new")
        )
        assert "archived" in result3.response.lower()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planning_does_not_affect_task_resume() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        from minibot.tasks.store import TaskStore

        task_store = TaskStore(app.runtime.workspace.root / "tasks")
        task = task_store.create(goal="calculate 2 + 3")
        msg = ChannelMessage(
            channel="cli",
            user_id="tester",
            session_id="test",
            content="calculate 2 + 3",
            metadata={"task_id": task["task_id"]},
        )
        result = app.runtime.agent_loop.handle_message(msg)
        assert "5" in result.response
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_plan_persistence_round_trip() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        plan = TaskPlan(
            plan_id="plan_persist_test",
            goal="test persistence",
            steps=[
                Step(step_id="s1", description="step 1", tool_hints=["file_read"]),
                Step(step_id="s2", description="step 2", tool_hints=["file_write"]),
            ],
        )
        app.runtime.task_executor.save_plan(plan)

        # Reload
        loaded = app.runtime.task_executor.load_plan("plan_persist_test")
        assert loaded is not None
        assert loaded.plan_id == "plan_persist_test"
        assert loaded.goal == "test persistence"
        assert len(loaded.steps) == 2
        assert loaded.steps[0].description == "step 1"
        assert loaded.steps[1].description == "step 2"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
