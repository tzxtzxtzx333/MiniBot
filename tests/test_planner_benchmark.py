"""Planner benchmark profile tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.evals.benchmark_runner import BenchmarkRunner
from minibot.json_utils import load_json_file

ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(**overrides: object) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    shutil.copytree(ROOT / "benchmarks", temp_root / "benchmarks")
    for name in ("examples", "reports"):
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


# ---------------------------------------------------------------------------
# Planner benchmark tests
# ---------------------------------------------------------------------------


def test_planner_profile_is_registered() -> None:
    """--profile planner should be accepted by the benchmark runner."""
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        assert report["benchmark_profile"] == "planner"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_report_has_top_level_fields() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        assert "planner_case_count" in report
        assert "planner_passed_count" in report
        assert "planner_pass_rate" in report
        assert "avg_plan_steps" in report
        assert "avg_evidence_count" in report
        assert "replan_count" in report
        assert report["planner_case_count"] >= 5  # 4 synthetic + 4 real planner
        assert "real_planner_case_count" in report
        assert "real_planner_passed_count" in report
        assert "real_planner_pass_rate" in report
        assert "planner_real_path_count" in report
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_file_report_001_passes() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        # Benchmark sandbox is isolated — copy README.md so file_read succeeds
        sandbox = app.runtime.workspace.sandbox_dir
        shutil.copy2(ROOT / "README.md", sandbox / "README.md")
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        result = next(r for r in report["results"] if r["id"] == "planner_file_report_001")
        assert result["passed"] is True
        assert result["status"] == "passed"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_approval_resume_001_passes() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        result = next(r for r in report["results"] if r["id"] == "planner_approval_resume_001")
        assert result["passed"] is True
        # Preloaded approval should result in file_write success
        tool_trace = result.get("tool_trace", [])
        assert any(t.get("tool_name") == "file_write" for t in tool_trace)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_evidence_context_001_passes() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        sandbox = app.runtime.workspace.sandbox_dir
        shutil.copy2(ROOT / "README.md", sandbox / "README.md")
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        result = next(r for r in report["results"] if r["id"] == "planner_evidence_context_001")
        assert result["passed"] is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_profile_does_not_break_safety() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="safety", mode="fake")
        assert report["benchmark_profile"] == "safety"
        assert "safety_case_count" in report
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_profile_does_not_break_multiround() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="multiround", mode="fake")
        assert report["benchmark_profile"] == "multiround"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_planner_metrics_in_compare() -> None:
    from minibot.evals.compare_reports import ReportComparator

    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(profile="planner", mode="fake")
        report_path = temp_root / "reports" / "planner_compare_test.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        comparator = ReportComparator()
        result = comparator.compare(report_path, report_path)
        metric_changes = result.get("metric_changes", {})
        assert "planner_pass_rate" in metric_changes
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_report_exists() -> None:
    evidence_path = ROOT / "docs" / "evidence" / "run_fake_planner.json"
    assert evidence_path.exists(), f"Missing evidence: {evidence_path}"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload.get("benchmark_profile") == "planner"
    assert payload.get("planner_case_count", 0) >= 4
