"""Experiment runner tests — schema, config loading, CLI, non-interference."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.evals.experiment_report import summarize_reports
from minibot.evals.experiment_runner import ExperimentRunner
from minibot.json_utils import load_json_file

ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(**overrides: object) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    # Copy experiments dir
    exp_src = ROOT / "experiments"
    if exp_src.is_dir():
        shutil.copytree(exp_src, temp_root / "experiments")
    config_path = temp_root / "configs" / "minibot.json"
    config = load_json_file(config_path)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_root / "configs" / "hooks.json").write_text(
        json.dumps({"hooks": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return temp_root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_experiment_runner_loads_config() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        config = runner._load_config("context_ablation")
        assert "baseline" in config
        assert "current" in config
        assert config["baseline"]["enable_history_retrieval"] is False
        assert config["current"]["enable_history_retrieval"] is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_experiment_runner_loads_cases() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        cases = runner._load_cases("context_ablation")
        assert len(cases) == 9
        assert cases[0]["id"].startswith("ctx_abl_")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_experiment_runner_toggles_context() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        cb = app.runtime.agent_loop.context_builder
        assert cb.enable_history_retrieval is True  # default
        runner._apply_context_config({"enable_history_retrieval": False})
        assert cb.enable_history_retrieval is False
        runner._restore_context()
        assert cb.enable_history_retrieval is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_experiment_report_schema() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run("context_ablation", mode="fake")
        assert "experiment" in report
        assert report["experiment"] == "context_ablation"
        assert "summary" in report
        assert "results" in report
        assert "baseline_config" in report
        assert "current_config" in report
        assert report["total_cases"] == 9
        summ = report["summary"]
        assert "avg_context_chars_baseline" in summ
        assert "avg_context_chars_current" in summ
        assert "context_reduction_rate" in summ
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_experiment_results_have_paired_metrics() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run("context_ablation", mode="fake")
        for r in report["results"]:
            assert "baseline_metrics" in r
            assert "current_metrics" in r
            if r["status"] == "skipped":
                assert r["baseline_metrics"] == {}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_summarize_reads_only_report() -> None:
    temp_root = _prepare_temp_root()
    try:
        report_path = temp_root / "reports" / "test_summary.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "experiment": "context_ablation",
                    "mode": "fake",
                    "total_cases": 1,
                    "completed_cases": 1,
                    "passed_cases": 1,
                    "failed_metric_missing": 0,
                    "failed_expectation": 0,
                    "skipped_cases": 0,
                    "pass_rate": 1.0,
                    "summary": {
                        "avg_context_chars_baseline": 1000,
                        "avg_context_chars_current": 800,
                        "context_reduction_rate": 0.2,
                    },
                    "results": [
                        {
                            "id": "t1",
                            "status": "completed",
                            "passed": True,
                            "baseline_metrics": {"context_chars": 1000},
                            "current_metrics": {"context_chars": 800},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        md = summarize_reports([report_path])
        assert "context_ablation" in md
        assert "0.2000" in md
        assert "800" in md
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_experiments_cli_list() -> None:
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-m", "minibot", "experiments", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=env,
        timeout=30,
    )
    assert r.returncode == 0
    assert "context_ablation" in r.stdout


def test_experiments_does_not_affect_benchmark() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = ExperimentRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        runner.run("context_ablation", mode="fake")
        # Context should be restored
        cb = app.runtime.agent_loop.context_builder
        assert cb.enable_history_retrieval is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
