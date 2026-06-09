from __future__ import annotations

import json
import os
import shutil
import urllib.error
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.evals.benchmark_runner import BenchmarkRunner
from minibot.evals.compare_reports import ReportComparator
from minibot.evals.metrics import summarize_case_metrics
from minibot.evals.model_verifier import ModelVerifier
from minibot.evals.report_writer import ReportWriter
from minibot.evals.rule_verifier import RuleVerifier
from minibot.governance.approval_store import ApprovalStore
from minibot.harness.model_client import load_model_client

ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root() -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    for name in ("configs", "benchmarks", "examples", "reports"):
        source = ROOT / name
        target = temp_root / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return temp_root


def test_rule_verifier_checks_structured_expectations() -> None:
    verifier = RuleVerifier()
    run_record = {
        "final_response": "MiniBot tool result: 5",
        "tool_calls": [{"tool_name": "calculator", "arguments": {"expression": "2 + 3"}}],
        "tool_results": [
            {"tool_name": "calculator", "status": "success", "failure_category": None}
        ],
        "failure_category": None,
        "retry_count": 0,
        "partial_success": False,
        "downgrade_reason": None,
    }
    passed, reason = verifier.verify(
        run_record,
        [
            "tool_call_contains:calculator",
            "tool_result_status:calculator:success",
            "final_response_contains:MiniBot tool result: 5",
        ],
    )
    assert passed is True
    assert "matched" in reason


def test_rule_verifier_checks_tool_result_metadata_expectation() -> None:
    verifier = RuleVerifier()
    run_record = {
        "tool_results": [
            {
                "tool_name": "calculator",
                "status": "success",
                "metadata": {"deduplicated": True},
            }
        ]
    }
    passed, _ = verifier.verify(run_record, ["tool_result_metadata:calculator:deduplicated:true"])
    assert passed is True


def test_rule_verifier_supports_context_metric_expectations() -> None:
    verifier = RuleVerifier()
    run_record = {
        "context_metrics": {
            "prompt_tokens": 321,
            "context_chars": 1284,
            "key_facts_preserved": True,
        },
        "avg_prompt_tokens": 321.0,
    }
    passed, reason = verifier.verify(
        run_record,
        [
            "context_metrics_present",
            "key_facts_preserved:true",
            "avg_prompt_tokens_present:0",
        ],
    )
    assert passed is True
    assert "matched 3/3 rules" in reason


def test_rule_verifier_reports_missing_context_metric_requirements() -> None:
    verifier = RuleVerifier()
    passed, reason = verifier.verify(
        {"context_metrics": {"prompt_tokens": 99}},
        ["context_metrics_present", "key_facts_preserved:true", "avg_prompt_tokens_present:0"],
    )
    assert passed is False
    assert "key_facts_preserved:true" in reason
    assert "context_metrics_present" in reason


def test_rule_verifier_supports_final_response_not_empty_from_final_response() -> None:
    verifier = RuleVerifier()
    passed, reason = verifier.verify(
        {"final_response": "MiniBot tool result: ok"}, ["final_response_not_empty"]
    )
    assert passed is True
    assert "matched 1/1 rules" in reason


def test_rule_verifier_supports_final_response_not_empty_from_response() -> None:
    verifier = RuleVerifier()
    passed, _ = verifier.verify(
        {"response": "MiniBot tool blocked: shell_exec"}, ["final_response_not_empty"]
    )
    assert passed is True


def test_rule_verifier_supports_final_response_not_empty_for_tool_only_response() -> None:
    verifier = RuleVerifier()
    passed, _ = verifier.verify(
        {"response": "MiniBot tool approval required: file_write"}, ["final_response_not_empty"]
    )
    assert passed is True


def test_rule_verifier_rejects_empty_final_response() -> None:
    verifier = RuleVerifier()
    passed, reason = verifier.verify(
        {"final_response": "   ", "response": ""}, ["final_response_not_empty"]
    )
    assert passed is False
    assert "final_response_not_empty" in reason


def test_model_verifier_fake_mode_returns_reason() -> None:
    verifier = ModelVerifier(mode="fake")
    result = verifier.verify(
        final_response="MiniBot echo: hello",
        expected_behavior=["final_response_contains:MiniBot echo:"],
        run_record={"tool_calls": [], "tool_results": []},
    )
    assert result["used_model"] is False
    assert result["passed"] is True
    assert result["reason"]
    assert result["verifier_mode"] == "fake"
    assert result["fake_verifier"] is True


def test_metrics_summary_uses_runtime_results() -> None:
    summary = summarize_case_metrics(
        case_results=[
            {
                "latency_ms": 10.0,
                "tool_rounds": 1,
                "failure_category": None,
                "tool_trace": [{"tool_name": "calculator"}],
                "verifier_reason": "ok",
                "retry_count": 0,
                "partial_success": False,
                "downgrade_reason": None,
                "passed": True,
                "counted_in_pass_rate": True,
            },
            {
                "latency_ms": 30.0,
                "tool_rounds": 2,
                "failure_category": "docker_unavailable",
                "tool_trace": [{"tool_name": "python_exec"}],
                "verifier_reason": "fallback",
                "retry_count": 2,
                "partial_success": True,
                "downgrade_reason": "retry_exhausted",
                "passed": False,
                "counted_in_pass_rate": True,
            },
        ]
    )
    assert summary["pass_rate"] == 0.5
    assert summary["avg_latency"] == 20.0
    assert summary["avg_tool_rounds"] == 1.5


def test_report_writer_outputs_json_and_markdown() -> None:
    temp_root = _prepare_temp_root()
    try:
        writer = ReportWriter()
        report = {
            "phase": "eval_test",
            "run_mode": "fake",
            "benchmark_scope": "core",
            "benchmark_case_count": 83,
            "benchmark_case_count_by_profile": {
                "default": 63,
                "execution": 5,
                "all-integrations": 6,
                "approval": 1,
                "safety": 8,
            },
            "benchmark_case_count_by_category": {
                "channel": 7,
                "context": 12,
                "memory": 13,
                "reasoning": 9,
                "regression": 11,
                "safety": 13,
                "tools": 18,
            },
            "context_case_count": 4,
            "avg_prompt_tokens": 1200.0,
            "avg_context_chars": 4800.0,
            "avg_dynamic_context_chars": 3600.0,
            "avg_dynamic_context_tokens": 900.0,
            "avg_history_chars": 1600.0,
            "avg_memory_chars": 900.0,
            "avg_archive_chars": 700.0,
            "avg_tool_specs_chars": 1200.0,
            "token_estimator": "ceil_len_div_4",
            "model_provider": "fake",
            "model_name": "fake",
            "fake_model": True,
            "verifier_mode": "fake",
            "fake_verifier": True,
            "verifier_provider": "fake",
            "verifier_model_name": "fake",
            "verifier_config_source": "dedicated",
            "docker_available": False,
            "total_cases": 2,
            "pass_rate": 0.5,
            "mock_tools_used": ["weather"],
            "real_tools_used": ["calculator"],
            "mcp_tools_used": ["map_route"],
            "missing_capabilities": ["docker_unavailable"],
            "capability_status": {"partial_success": "passed"},
            "external_integrations": {"weather": "mock", "map_route": "mcp"},
            "human_review": {"pending_count": 1, "approved_count": 2, "rejected_count": 3},
            "safety_case_count": 8,
            "safety_passed_count": 8,
            "safety_pass_rate": 1.0,
            "results": [],
        }
        json_path = temp_root / "reports" / "eval-report.json"
        paths = writer.write(json_path, report)
        assert paths["json"].exists()
        assert paths["markdown"].exists()
        markdown = paths["markdown"].read_text(encoding="utf-8")
        assert "eval_test" in markdown
        assert "pass_rate" in markdown
        assert "tool_rounds" in markdown
        assert "benchmark_case_count: 83" in markdown
        assert "avg_prompt_tokens: 1200.0" in markdown
        assert "avg_dynamic_context_tokens: 900.0" in markdown
        assert "token_estimator: ceil_len_div_4" in markdown
        assert "profile.safety: 8" in markdown
        assert "category.tools: 18" in markdown
        assert "run_mode: fake" in markdown
        assert "mock_tools_used: weather" in markdown
        assert "mcp_tools_used: map_route" in markdown
        assert "verifier_mode: fake" in markdown
        assert "pending_count: 1" in markdown
        assert "safety_case_count: 8" in markdown
        assert "safety_pass_rate: 1.0" in markdown
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_report_comparator_summarizes_new_and_fixed_failures() -> None:
    temp_root = _prepare_temp_root()
    try:
        comparator = ReportComparator()
        left = temp_root / "reports" / "left.json"
        right = temp_root / "reports" / "right.json"
        left.write_text(
            json.dumps(
                {
                    "pass_rate": 0.5,
                    "avg_latency": 20.0,
                    "avg_prompt_tokens": 1200.0,
                    "avg_context_chars": 4800.0,
                    "avg_dynamic_context_tokens": 1000.0,
                    "tool_rounds": 1.0,
                    "retry_count": 0.0,
                    "partial_success": 0,
                    "capability_status": {"real_model": "missing"},
                    "mock_tools_used": ["weather"],
                    "real_tools_used": ["calculator"],
                    "results": [
                        {"id": "a", "passed": True},
                        {"id": "b", "passed": False},
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        right.write_text(
            json.dumps(
                {
                    "pass_rate": 0.75,
                    "avg_latency": 18.0,
                    "avg_prompt_tokens": 900.0,
                    "avg_context_chars": 3600.0,
                    "avg_dynamic_context_tokens": 700.0,
                    "tool_rounds": 2.0,
                    "retry_count": 1.0,
                    "partial_success": 1,
                    "capability_status": {"real_model": "passed"},
                    "mock_tools_used": ["weather", "map_route"],
                    "real_tools_used": ["calculator", "python_exec"],
                    "results": [
                        {"id": "a", "passed": False},
                        {"id": "b", "passed": True},
                        {"id": "c", "passed": False},
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result = comparator.compare(left, right)
        assert "a" in result["new_failures"]
        assert "b" in result["fixed_failures"]
        assert result["metric_changes"]["pass_rate"]["delta"] == 0.25
        assert result["capability_status_changes"]["real_model"]["right"] == "passed"
        assert "map_route" in result["mock_tools_used_changes"]["added"]
        assert "python_exec" in result["real_tools_used_changes"]["added"]
        assert result["avg_prompt_tokens_before"] == 1200.0
        assert result["avg_prompt_tokens_after"] == 900.0
        assert result["token_reduction_rate"] == 0.25
        assert result["avg_dynamic_context_tokens_before"] == 1000.0
        assert result["avg_dynamic_context_tokens_after"] == 700.0
        assert result["dynamic_token_reduction_rate"] == 0.3
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_writes_custom_report_and_metrics() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report_path = temp_root / "reports" / "run_v1.json"
        report = runner.run(category="memory", report_path=report_path, mode="fake")
        assert report["report_path"] == "reports/latest.json"
        assert report["run_mode"] == "fake"
        assert report["benchmark_profile"] == "default"
        assert report["benchmark_case_count"] >= 70
        assert report["benchmark_case_count_by_profile"]["default"] >= 1
        assert report["benchmark_case_count_by_category"]["tools"] >= 1
        assert report["fake_model"] is True
        assert report["model_provider"] == "fake"
        assert report["counted_cases"] >= 1
        assert "avg_latency" in report
        assert "avg_tool_rounds" in report
        assert "tool_rounds" in report
        assert "retry_count" in report
        assert "mock_tools_used" in report
        assert "real_tools_used" in report
        assert "mcp_tools_used" in report
        assert "safety_case_count" in report
        assert "safety_pass_rate" in report
        assert "avg_prompt_tokens" in report
        assert "avg_dynamic_context_tokens" in report
        assert "context_case_count" in report
        assert report["token_estimator"] == "ceil_len_div_4"
        assert report["verifier_mode"] == "fake"
        assert report["fake_verifier"] is True
        assert report["capability_status"]["model_verifier"] == "fake"
        assert report_path.exists()
        assert report_path.with_suffix(".md").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_mode_missing_config_generates_report_without_fake_fallback(
    monkeypatch,
) -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        for key in (
            "MINIBOT_MODEL_PROVIDER",
            "MINIBOT_MODEL_BASE_URL",
            "MINIBOT_MODEL_API_KEY",
            "MINIBOT_MODEL_NAME",
            "MINIBOT_BASE_URL",
            "MINIBOT_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        report_path = temp_root / "reports" / "run_real_v1.json"
        report = runner.run(mode="real", scope="core", report_path=report_path)
        assert report["run_mode"] == "real"
        assert report["benchmark_scope"] == "core"
        assert report["fake_model"] is False
        assert "deepseek_config_missing" in report["missing_capabilities"]
        assert report["capability_status"]["real_model"] == "missing"
        assert report["capability_status"]["model_verifier"] == "fake"
        assert report["pass_rate"] == 0.0
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["missing_capabilities"] == report["missing_capabilities"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_mode_marks_docker_unavailable(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
        monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("MINIBOT_MODEL_API_KEY", "test-key")
        monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")
        monkeypatch.setattr(runner, "_docker_available", lambda: False)
        report = runner.run(mode="real", scope="core")
        assert report["docker_available"] is False
        assert "docker_unavailable" in report["missing_capabilities"]
        assert report["capability_status"]["docker_sandbox"] == "unavailable"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_reports_external_provider_statuses(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
        monkeypatch.delenv("MINIBOT_WEATHER_API_KEY", raising=False)
        monkeypatch.setenv("MINIBOT_WEB_SEARCH_PROVIDER", "tavily")
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
        monkeypatch.delenv("MINIBOT_AMAP_MCP_ENDPOINT", raising=False)
        monkeypatch.delenv("MINIBOT_AMAP_MCP_API_KEY", raising=False)
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(category="tools", mode="fake")
        assert report["external_integrations"]["web_fetch"] == "real"
        assert report["external_integrations"]["web_search"] == "missing"
        assert report["external_integrations"]["weather"] == "missing"
        assert report["external_integrations"]["map_route"] == "missing"
        assert report["external_integrations"]["map_poi_search"] == "missing"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_reports_human_review_counts() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = ApprovalStore(app.runtime.workspace.approvals_dir)
        pending = store.create_pending(
            session_id="eval-pending",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "[REDACTED]"},
            risk_level="gray",
            reason="approval_denied",
        )
        approved = store.create_pending(
            session_id="eval-approved",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/approved.txt", "content": "[REDACTED]"},
            risk_level="gray",
            reason="approval_denied",
        )
        rejected = store.create_pending(
            session_id="eval-rejected",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/rejected.txt", "content": "[REDACTED]"},
            risk_level="gray",
            reason="approval_denied",
        )
        store.approve(str(approved["approval_id"]))
        store.reject(str(rejected["approval_id"]))
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(category="context", mode="fake")
        assert report["human_review"] == {
            "pending_count": 1,
            "approved_count": 1,
            "rejected_count": 1,
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_human_review_summary_ignores_malformed_jsonl() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        approvals_dir = app.runtime.workspace.approvals_dir
        (approvals_dir / "pending.jsonl").write_text(
            '\ufeff{"approval_id":"ok-1","status":"pending","request_signature":"sig-1"}\n{bad json\n',
            encoding="utf-8",
        )
        (approvals_dir / "resolved.jsonl").write_text(
            '{"approval_id":"ok-2","status":"approved","request_signature":"sig-2"}\n{bad json\n',
            encoding="utf-8",
        )
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(category="context", mode="fake")
        assert report["human_review"]["pending_count"] == 1
        assert report["human_review"]["approved_count"] == 1
        assert report["human_review"]["rejected_count"] == 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_normalizes_fake_echo_assertion_for_real_mode() -> None:
    normalized = BenchmarkRunner._normalize_case(
        {
            "id": "demo",
            "category": "channel",
            "input": "hello",
            "expected_behavior": ["final_response_contains:MiniBot echo:"],
        },
        "real",
    )
    assert normalized["expected_behavior"] == ["final_response_not_empty"]


def test_benchmark_runner_execution_scope_temporarily_auto_approves_graylist() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        with runner._benchmark_policy_override("execution"):
            decision = app.runtime.tool_dispatcher.approval_manager.decide(
                "file_write", requires_approval=True
            )
            assert decision.approved is True
        decision_after = app.runtime.tool_dispatcher.approval_manager.decide(
            "file_write", requires_approval=True
        )
        assert decision_after.approved is False
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_filters_cases_by_profile() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        approval_cases = runner._load_cases(None, "core", "approval")
        execution_cases = runner._load_cases(None, "core", "execution")
        integration_cases = runner._load_cases(None, "core", "all-integrations")
        real_agent_cases = runner._load_cases(None, "core", "real-agent")
        safety_cases = runner._load_cases(None, "core", "safety")
        context_baseline_cases = runner._load_cases(None, "core", "context-baseline")
        context_optimized_cases = runner._load_cases(None, "core", "context-optimized")
        realistic_baseline_cases = runner._load_cases(None, "core", "context-realistic-baseline")
        realistic_optimized_cases = runner._load_cases(None, "core", "context-realistic-optimized")
        assert approval_cases
        assert any("approval" in list(case.get("profiles", [])) for case in approval_cases)
        assert execution_cases
        assert any("execution" in list(case.get("profiles", [])) for case in execution_cases)
        assert integration_cases
        assert any(
            "all-integrations" in list(case.get("profiles", [])) for case in integration_cases
        )
        assert len(real_agent_cases) >= 10
        assert all("real-agent" in list(case.get("profiles", [])) for case in real_agent_cases)
        assert len(safety_cases) >= 7
        assert all("safety" in list(case.get("profiles", [])) for case in safety_cases)
        assert context_baseline_cases
        assert context_optimized_cases
        assert {case["id"] for case in context_baseline_cases} == {
            case["id"] for case in context_optimized_cases
        }
        assert len(realistic_baseline_cases) >= 5
        assert {case["id"] for case in realistic_baseline_cases} == {
            case["id"] for case in realistic_optimized_cases
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_agent_profile_marks_synthetic_and_preapproved_cases() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        real_agent_cases = runner._load_cases(None, "core", "real-agent")
        ids = {case["id"] for case in real_agent_cases}
        assert "real_agent_partial_success_001" in ids
        assert "real_agent_python_exec_001" in ids
        assert "real_agent_file_write_read_001" in ids
        partial_case = next(
            case for case in real_agent_cases if case["id"] == "real_agent_partial_success_001"
        )
        python_case = next(
            case for case in real_agent_cases if case["id"] == "real_agent_python_exec_001"
        )
        file_case = next(
            case for case in real_agent_cases if case["id"] == "real_agent_file_write_read_001"
        )
        assert partial_case["synthetic_tool_plan"]
        assert python_case["preloaded_approval"]["status"] == "approved"
        assert file_case["preloaded_approval"]["status"] == "approved"
        assert (
            "tool_result_metadata:file_write:approval_status:approved"
            in file_case["expected_behavior"]
        )
        assert "tool_result_metadata:file_write:risk_level:gray" in file_case["expected_behavior"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_builds_real_agent_report_fields() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner._build_report(
            cases=[
                {
                    "id": "real_agent_search_summary_001",
                    "category": "tools",
                    "profiles": ["real-agent"],
                },
                {
                    "id": "real_agent_partial_success_001",
                    "category": "reasoning",
                    "profiles": ["real-agent"],
                },
            ],
            results=[
                {
                    "id": "real_agent_search_summary_001",
                    "category": "tools",
                    "status": "passed",
                    "passed": True,
                    "counted_in_pass_rate": True,
                    "latency_ms": 100.0,
                    "tool_rounds": 1,
                    "failure_category": None,
                    "tool_trace": [],
                    "verifier_reason": "ok",
                    "retry_count": 0,
                    "partial_success": False,
                    "downgrade_reason": None,
                    "human_review": False,
                },
                {
                    "id": "real_agent_partial_success_001",
                    "category": "reasoning",
                    "status": "passed",
                    "passed": True,
                    "counted_in_pass_rate": True,
                    "latency_ms": 120.0,
                    "tool_rounds": 2,
                    "failure_category": None,
                    "tool_trace": [],
                    "verifier_reason": "ok",
                    "retry_count": 0,
                    "partial_success": True,
                    "downgrade_reason": None,
                    "human_review": False,
                },
            ],
            run_records=[],
            metric_summary=summarize_case_metrics(
                [
                    {
                        "passed": True,
                        "counted_in_pass_rate": True,
                        "latency_ms": 100.0,
                        "tool_rounds": 1,
                        "failure_category": None,
                        "tool_trace": [],
                        "verifier_reason": "ok",
                        "retry_count": 0,
                        "partial_success": False,
                        "downgrade_reason": None,
                    },
                    {
                        "passed": True,
                        "counted_in_pass_rate": True,
                        "latency_ms": 120.0,
                        "tool_rounds": 2,
                        "failure_category": None,
                        "tool_trace": [],
                        "verifier_reason": "ok",
                        "retry_count": 0,
                        "partial_success": True,
                        "downgrade_reason": None,
                    },
                ]
            ),
            mode="real",
            scope="core",
            profile="real-agent",
            preflight={
                "model_provider": "deepseek",
                "model_name": "deepseek-chat",
                "fake_model": False,
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_provider": "deepseek",
                "verifier_model_name": "deepseek-chat",
                "verifier_config_source": "model_config",
                "docker_available": True,
                "missing_capabilities": [],
                "external_integrations": {
                    "feishu": "missing",
                    "web_fetch": "real",
                    "web_search": "real",
                    "weather": "real",
                    "map_route": "mcp",
                    "map_poi_search": "mcp",
                },
            },
            model_errors=[],
        )
        assert report["benchmark_profile"] == "real-agent"
        assert report["real_agent_case_count"] == 2
        assert report["real_agent_passed_count"] == 2
        assert report["real_agent_pass_rate"] == 1.0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_reports_safety_counts() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(category="safety", mode="fake", profile="safety")
        assert report["benchmark_profile"] == "safety"
        assert report["total_cases"] >= 7
        assert report["safety_case_count"] >= 7
        assert report["safety_passed_count"] <= report["safety_case_count"]
        assert "safety_pass_rate" in report
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_reports_context_metrics_for_context_optimized_profile() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(mode="fake", profile="context-optimized")
        assert report["benchmark_profile"] == "context-optimized"
        assert report["context_case_count"] >= 1
        assert report["avg_prompt_tokens"] >= 0
        assert report["avg_dynamic_context_tokens"] >= 0
        assert report["token_estimator"] == "ceil_len_div_4"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_context_benchmark_profiles_share_cases_and_optimized_reduces_dynamic_tokens() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        baseline = runner.run(mode="fake", profile="context-baseline")
        optimized = runner.run(mode="fake", profile="context-optimized")
        assert baseline["benchmark_profile"] == "context-baseline"
        assert optimized["benchmark_profile"] == "context-optimized"
        assert baseline["context_case_count"] == optimized["context_case_count"]
        assert optimized["avg_dynamic_context_tokens"] < baseline["avg_dynamic_context_tokens"]
        assert optimized["avg_prompt_tokens"] < baseline["avg_prompt_tokens"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_realistic_context_benchmark_profiles_share_cases_and_optimized_reduces_dynamic_tokens() -> (
    None
):
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        baseline = runner.run(mode="fake", profile="context-realistic-baseline")
        optimized = runner.run(mode="fake", profile="context-realistic-optimized")
        assert baseline["benchmark_profile"] == "context-realistic-baseline"
        assert optimized["benchmark_profile"] == "context-realistic-optimized"
        assert baseline["context_case_count"] == optimized["context_case_count"]
        assert baseline["context_case_count"] >= 5
        assert optimized["avg_dynamic_context_tokens"] < baseline["avg_dynamic_context_tokens"]
        assert optimized["avg_prompt_tokens"] < baseline["avg_prompt_tokens"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_realistic_context_profiles_pass_on_metrics_without_text_quality_dependency() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        baseline = runner.run(mode="fake", profile="context-realistic-baseline")
        optimized = runner.run(mode="fake", profile="context-realistic-optimized")
        assert baseline["benchmark_profile"] == "context-realistic-baseline"
        assert optimized["benchmark_profile"] == "context-realistic-optimized"
        assert baseline["failed_case_count"] == 0
        assert optimized["failed_case_count"] == 0
        assert baseline["pass_rate"] == 1.0
        assert optimized["pass_rate"] == 1.0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_prepare_case_state_skips_unreadable_original_archives(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        workspace = app.runtime.workspace
        archive_path = workspace.archives_dir / "missing-archive.md"
        archive_path.write_text("stale archive", encoding="utf-8")
        original_glob = workspace.archives_dir.glob
        original_read_text = Path.read_text

        def fake_read_text(self: Path, *args, **kwargs):  # noqa: ANN001
            if self == archive_path:
                raise FileNotFoundError(str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        case = {
            "id": "context_case",
            "context_seed": {
                "memory": "- User prefers concise replies\n",
                "history": "user: hi\nassistant: hello\n",
                "archives": [{"name": "seeded.md", "content": "# ARCHIVE\n\nseeded"}],
            },
        }
        with runner._prepare_case_state(case):
            assert (workspace.archives_dir / "seeded.md").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_collects_provider_usage_from_trace_metadata() -> None:
    usage = BenchmarkRunner._collect_tool_usage(
        [
            {
                "tool_trace": [
                    {
                        "tool_name": "weather",
                        "metadata": {
                            "provider_status": "real",
                            "real_provider": True,
                            "mock_provider": False,
                        },
                    },
                    {
                        "tool_name": "web_search",
                        "metadata": {
                            "provider_status": "real",
                            "real_provider": True,
                            "mock_provider": False,
                        },
                    },
                    {
                        "tool_name": "map_route",
                        "metadata": {
                            "provider_status": "mcp",
                            "mcp_provider": True,
                            "mock_provider": False,
                        },
                    },
                    {
                        "tool_name": "map_poi_search",
                        "metadata": {
                            "provider_status": "mcp",
                            "mcp_provider": True,
                            "mock_provider": False,
                        },
                    },
                ]
            }
        ]
    )
    assert usage["mock_tools_used"] == []
    assert "weather" in usage["real_tools_used"]
    assert "web_search" in usage["real_tools_used"]
    assert "map_route" in usage["real_tools_used"]
    assert "map_poi_search" in usage["real_tools_used"]
    assert usage["mcp_tools_used"] == ["map_poi_search", "map_route"]


def test_benchmark_runner_uses_trace_metadata_for_external_integrations() -> None:
    merged = BenchmarkRunner._merge_external_integrations(
        defaults={
            "web_fetch": "real",
            "web_search": "mock",
            "weather": "mock",
            "map_route": "mock",
            "map_poi_search": "mock",
        },
        results=[
            {
                "tool_trace": [
                    {
                        "tool_name": "weather",
                        "metadata": {"provider_status": "real", "real_provider": True},
                    },
                    {
                        "tool_name": "web_search",
                        "metadata": {"provider_status": "real", "real_provider": True},
                    },
                    {
                        "tool_name": "map_route",
                        "metadata": {"provider_status": "mcp", "mcp_provider": True},
                    },
                    {
                        "tool_name": "map_poi_search",
                        "metadata": {"provider_status": "mcp", "mcp_provider": True},
                    },
                ]
            }
        ],
    )
    assert merged["weather"] == "real"
    assert merged["web_search"] == "real"
    assert merged["map_route"] == "mcp"
    assert merged["map_poi_search"] == "mcp"


def test_benchmark_runner_real_mode_marks_model_http_error_without_crashing(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
        monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("MINIBOT_MODEL_API_KEY", "test-key")
        monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")
        runner.agent_loop.model_client = load_model_client(temp_root, "real")

        class _FakeHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://api.deepseek.com/chat/completions",
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=None,
                )

            def read(self) -> bytes:
                return b'{"error":{"message":"bad request"}}'

        def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
            raise _FakeHttpError()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        report = runner.run(mode="real", scope="core")
        assert report["run_mode"] == "real"
        assert report["fake_model"] is False
        assert report["capability_status"]["real_model"] == "failed"
        assert report["model_error"] == "model_http_error"
        assert report["results"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_uses_actual_run_trace_fields() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="eval-trace", content="计算 2 + 3"
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        assert run_record["tool_trace"]
        assert run_record["verifier_reason"]
        assert "subagent_trace" in run_record
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_verifier_missing_does_not_block_case_execution(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        report = runner.run(category="memory", mode="fake")
        assert report["verifier_mode"] == "real"
        assert report["fake_verifier"] is False
        assert "verifier_config_missing" in report["missing_capabilities"]
        assert report["capability_status"]["model_verifier"] == "missing"
        assert report["results"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_verifier_http_error_marks_failed_without_crashing(
    monkeypatch,
) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
        monkeypatch.setenv("MINIBOT_VERIFIER_PROVIDER", "deepseek")
        monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
        monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )

        class _FakeHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://api.deepseek.com/chat/completions",
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=None,
                )

            def read(self) -> bytes:
                return b'{"error":"bad verifier request"}'

        def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
            raise _FakeHttpError()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        runner.model_verifier = ModelVerifier.from_project_root(temp_root)
        report = runner.run(category="memory", mode="fake")
        assert report["verifier_mode"] == "real"
        assert report["capability_status"]["model_verifier"] == "failed"
        assert report["verifier_error"] == "verifier_http_error"
        assert report["results"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_runner_real_verifier_case_failure_does_not_set_top_level_verifier_error(
    monkeypatch,
) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
        monkeypatch.setenv("MINIBOT_VERIFIER_PROVIDER", "deepseek")
        monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
        monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
        app = MiniBotApp(temp_root)
        runner = BenchmarkRunner(
            app.runtime.agent_loop, temp_root, verifier_agent=app.runtime.verifier_agent
        )
        runner.model_verifier = ModelVerifier.from_project_root(temp_root)

        def fake_verify(
            *, final_response: str, expected_behavior: list[str], run_record: dict[str, object]
        ) -> dict[str, object]:  # noqa: ARG001
            return {
                "used_model": True,
                "passed": False,
                "reason": "missing expected content",
                "failure_category": "missing_expected_content",
                "confidence": 0.8,
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_config_source": "dedicated",
            }

        runner.model_verifier.verify = fake_verify  # type: ignore[method-assign]
        report = runner.run(category="memory", mode="fake")
        assert report["verifier_mode"] == "real"
        assert report["capability_status"]["model_verifier"] == "passed"
        assert report["verifier_error"] is None
        assert report["failed_case_count"] >= 1
        assert "missing_expected_content" in report["failed_case_categories"]
        failed = next(item for item in report["results"] if item["status"] == "failed")
        assert failed["verifier_failure_category"] == "missing_expected_content"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
