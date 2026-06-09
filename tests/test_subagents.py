from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.evals.benchmark_runner import BenchmarkRunner
from minibot.subagents.memory_agent import MemoryAgent
from minibot.subagents.summarizer_agent import SummarizerAgent
from minibot.subagents.tool_agent import ToolAgent
from minibot.subagents.verifier_agent import VerifierAgent


ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root() -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    return temp_root


def test_memory_agent_classifies_long_term_memory_request() -> None:
    agent = MemoryAgent()
    decision = agent.assess("记住 我喜欢中文回答")
    assert decision["store_memory"] is True
    assert decision["store_history"] is True
    assert decision["memory_fact"] == "我喜欢中文回答"


def test_summarizer_agent_generates_archive_ready_summary_sections() -> None:
    agent = SummarizerAgent()
    archive = agent.summarize(
        history_text="user: 目标是整理项目\nassistant: MiniBot tool result: {'result': 5}\n",
        memory_text="# MEMORY\n\n- 喜欢中文回答\n",
    )
    assert archive["archive_mode"] == "fake"
    summary = str(archive["summary"])
    for heading in [
        "## 用户目标",
        "## 已完成任务",
        "## 未完成任务",
        "## 关键事实",
        "## 用户偏好",
        "## 工具调用结果",
        "## 后续建议",
    ]:
        assert heading in summary


def test_tool_agent_executes_plan_through_dispatcher() -> None:
    app = MiniBotApp(ROOT)
    plan = app.runtime.model_client.plan(
        ChannelMessage(channel="test", user_id="tester", session_id="tool-agent", content="计算 2 + 3"),
        {},
    )
    tool_agent = ToolAgent(app.runtime.tool_dispatcher)
    execution = tool_agent.execute_plan(plan)
    assert execution["tool_calls"][0]["tool_name"] == "calculator"
    assert execution["tool_results"][0]["status"] == "success"
    assert execution["tool_trace"][0]["status"] == "success"


def test_verifier_agent_generates_reason_from_user_goal() -> None:
    agent = VerifierAgent()
    verification = agent.verify(
        final_response="MiniBot tool result: 5",
        user_goal="计算 2 + 3",
        expected_behavior=None,
        tool_results=[{"tool_name": "calculator", "status": "success"}],
    )
    assert verification["verified"] is True
    assert verification["verifier_reason"]


def test_summarizer_agent_archive_is_persisted_and_traced() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="subagent-archive", content="hello archive")
        )
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="subagent-archive", content="/new")
        )
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        latest_archive = archives[-1].read_text(encoding="utf-8")
        assert "summary_by: SummarizerAgent" in latest_archive
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert any(item["agent"] == "SummarizerAgent" for item in record["subagent_trace"])
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_records_subagent_trace_and_verifier_reason() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="subagent-loop", content="计算 2 + 3")
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["verifier_reason"]
        agents = {item["agent"] for item in record["subagent_trace"]}
        assert {"MemoryAgent", "ToolAgent", "VerifierAgent"} <= agents
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_uses_verifier_agent_reason() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(app.runtime.agent_loop, ROOT, verifier_agent=app.runtime.verifier_agent)
        report = runner.run(profile="safety")  # 9 cases vs 100+, enough to prove verifier_reason
        non_pending = [item for item in report["results"] if item["status"] != "pending"]
        assert non_pending
        assert all(item["verifier_reason"] for item in non_pending)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
