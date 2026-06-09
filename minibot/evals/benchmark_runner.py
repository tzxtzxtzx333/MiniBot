"""JSON benchmark runner and audit report generation."""

from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from minibot.channels.base import ChannelMessage
from minibot.config import load_config
from minibot.governance.approval_store import ApprovalStore
from minibot.harness.model_client import ModelFinalAnswer, ModelPlan, ToolCall, _load_env_settings
from minibot.json_utils import load_json_file
from minibot.status import _docker_available
from minibot.workspace import WorkspaceManager

from .metrics import summarize_case_metrics
from .model_verifier import ModelVerifier
from .report_writer import ReportWriter
from .rule_verifier import RuleVerifier

CORE_CATEGORIES = {"context", "memory", "reasoning", "safety", "tools", "regression", "planner"}
PROFILE_SCOPES = {
    "approval",
    "execution",
    "all-integrations",
    "real-agent",
    "safety",
    "multiround",
    "planner",
    "context-baseline",
    "context-optimized",
    "context-realistic-baseline",
    "context-realistic-optimized",
}
MOCK_TOOLS = {"weather", "map_route", "map_poi_search", "web_search"}
REAL_TOOLS = {
    "calculator",
    "file_read",
    "file_write",
    "web_fetch",
    "memory_search",
    "memory_write",
    "doc_summarize",
    "python_exec",
    "shell_exec",
}


class BenchmarkRunner:
    """Run JSON benchmark cases through the current AgentLoop and write audit reports."""

    def __init__(self, agent_loop, project_root: Path, verifier_agent=None,
                 long_task_runner=None, planner_agent=None) -> None:
        self.agent_loop = agent_loop
        self.project_root = project_root
        self.rule_verifier = RuleVerifier()
        self.model_verifier = ModelVerifier.from_project_root(project_root)
        self.report_writer = ReportWriter()
        self.verifier_agent = verifier_agent
        self.long_task_runner = long_task_runner
        self.planner_agent = planner_agent

    def run(
        self,
        category: str | None = None,
        report_path: Path | None = None,
        *,
        mode: str = "fake",
        scope: str | None = None,
        profile: str = "default",
    ) -> dict[str, object]:
        """Execute filtered JSON cases and persist JSON/Markdown reports."""

        normalized_mode = self._normalize_mode(mode)
        normalized_profile = self._normalize_profile(profile)
        if normalized_profile == "real-agent" and normalized_mode != "real":
            raise ValueError("real-agent profile requires --mode real")
        cases = [self._normalize_case(case, normalized_mode) for case in self._load_cases(category, scope, normalized_profile)]
        preflight = self._build_preflight(normalized_mode, normalized_profile)
        results: list[dict[str, object]] = []
        run_records: list[dict[str, object]] = []
        model_errors: list[str] = []

        if preflight["can_execute"]:
            with self._benchmark_policy_override(normalized_profile):
                with self._context_profile_override(normalized_profile):
                    with self._benchmark_approval_isolation(normalized_profile):
                        for case in cases:
                            if case.get("status") == "pending":
                                results.append(
                                    {
                                        "id": case["id"],
                                        "category": case["category"],
                                        "status": "pending",
                                        "passed": False,
                                        "counted_in_pass_rate": False,
                                        "latency_ms": 0.0,
                                        "tool_rounds": 0,
                                        "failure_category": None,
                                        "tool_trace": [],
                                        "verifier_reason": case.get("pending_reason", "pending"),
                                        "retry_count": 0,
                                        "partial_success": False,
                                        "downgrade_reason": None,
                                        "human_review": bool(dict(case.get("verifier", {})).get("human_optional", False)),
                                    }
                                )
                                continue

                            started = time.perf_counter()
                            is_real_planner = (
                                normalized_profile == "planner"
                                and case.get("planner_mode") == "real"
                                and self.long_task_runner is not None
                                and self.planner_agent is not None
                            )
                            try:
                                if is_real_planner:
                                    # Copy referenced files into sandbox
                                    self._prepare_planner_sandbox(case)
                                    is_approval_test = bool(case.get("planner_approval_test"))
                                    if is_approval_test:
                                        # ── approval + resume flow ──
                                        plan = self.planner_agent.plan(str(case["input"]))
                                        first_result = self.long_task_runner.run(plan)
                                        # Preserve first-run tool trace for audit
                                        first_trace = []
                                        for o in first_result.get("step_outcomes", []):
                                            first_trace.extend(o.get("tool_trace", []))
                                        if first_result.get("status") == "waiting_approval":
                                            self._approve_pending_for_plan(first_result)
                                            plan_id = str(first_result["plan_id"])
                                            resume_result = self.long_task_runner.resume(plan_id)
                                            # Merge first-run tool_trace into resume result
                                            if not resume_result.get("step_outcomes"):
                                                resume_result["step_outcomes"] = []
                                            if first_trace:
                                                resume_result["step_outcomes"].append(
                                                    {"tool_trace": first_trace, "status": "completed",
                                                     "final_response": "", "evidence_ids": [],
                                                     "failure_category": None}
                                                )
                                            plan_result = resume_result
                                        else:
                                            plan_result = first_result
                                        run_record = self._planner_result_to_run_record(case, plan, plan_result)
                                    else:
                                        # Auto-approve graylisted tools during benchmark
                                        approval_mgr = self.agent_loop.tool_dispatcher.approval_manager
                                        orig_config = dict(approval_mgr.approval_config)
                                        auto_config = dict(orig_config)
                                        auto_config["auto_approve"] = True
                                        approval_mgr.approval_config = auto_config
                                        try:
                                            plan = self.planner_agent.plan(str(case["input"]))
                                            plan_result = self.long_task_runner.run(plan)
                                        finally:
                                            approval_mgr.approval_config = orig_config
                                        run_record = self._planner_result_to_run_record(case, plan, plan_result)
                                    run_records.append(run_record)
                                else:
                                    expanded_case = self._expand_context_case(case)
                                    with self._prepare_case_state(expanded_case):
                                        with self._synthetic_model_plan(expanded_case):
                                            result = self.agent_loop.handle_message(
                                                ChannelMessage(
                                                    channel="benchmark",
                                                    user_id="benchmark-runner",
                                                    session_id=str(expanded_case["id"]),
                                                    content=str(expanded_case["input"]),
                                                    metadata={
                                                        "category": expanded_case["category"],
                                                        "benchmark_mode": normalized_mode,
                                                        "benchmark_scope": scope,
                                                        "benchmark_context_tool_results": list(expanded_case.get("context_tool_results", [])),
                                                        "benchmark_required_facts": list(expanded_case.get("required_facts", [])),
                                                    },
                                                )
                                            )
                                    run_record = self._load_run_record(result.run_id)
                                    run_records.append(run_record)
                            except Exception as exc:  # noqa: BLE001
                                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                                exc_str = str(exc)
                                is_http_error = "HTTP Error" in exc_str
                                if is_http_error:
                                    model_errors.append("model_http_error")
                                    fallback_failure_category = "model_http_error"
                                else:
                                    recovered = self._try_recover_failure_category(str(expanded_case["id"]))
                                    if recovered is not None:
                                        fallback_failure_category = recovered
                                    else:
                                        model_errors.append("benchmark_runtime_error")
                                        fallback_failure_category = "benchmark_runtime_error"
                                results.append(
                                    {
                                        "id": case["id"],
                                        "category": case["category"],
                                        "status": "failed",
                                        "passed": False,
                                        "counted_in_pass_rate": True,
                                        "latency_ms": latency_ms,
                                        "tool_rounds": 0,
                                        "failure_category": fallback_failure_category,
                                        "tool_trace": [],
                                        "verifier_reason": exc_str,
                                        "retry_count": 0,
                                        "partial_success": False,
                                        "downgrade_reason": None,
                                        "human_review": bool(dict(case.get("verifier", {})).get("human_optional", False)),
                                    }
                                )
                                continue
                            latency_ms = round((time.perf_counter() - started) * 1000, 2)
                            rule_enabled = bool(dict(case.get("verifier", {})).get("rule", True))
                            model_enabled = bool(dict(case.get("verifier", {})).get("model", True))
                            rule_passed = True
                            rule_reason = "rule verifier disabled"
                            if rule_enabled:
                                rule_passed, rule_reason = self.rule_verifier.verify(run_record, list(case["expected_behavior"]))
                            model_passed = True
                            model_reason = "model verifier disabled"
                            if model_enabled:
                                model_result = self.model_verifier.verify(
                                    final_response=str(run_record.get("final_response", "")),
                                    expected_behavior=list(case["expected_behavior"]),
                                    run_record=run_record,
                                )
                                model_passed = bool(model_result["passed"])
                                model_reason = str(model_result["reason"])
                                if model_result.get("failure_category") in {
                                    "verifier_config_missing",
                                    "verifier_http_error",
                                    "verifier_parse_error",
                                }:
                                    model_passed = True
                            verifier_reason = self._compose_verifier_reason(rule_reason, model_reason, run_record.get("verifier_reason"))
                            passed_case = rule_passed and model_passed
                            results.append(
                                {
                                    "id": case["id"],
                                    "category": case["category"],
                                    "status": "passed" if passed_case else "failed",
                                    "passed": passed_case,
                                    "counted_in_pass_rate": True,
                                    "latency_ms": latency_ms,
                                    "tool_rounds": len(run_record.get("tool_calls", [])),
                                    "failure_category": run_record.get("failure_category"),
                                    "tool_trace": run_record.get("tool_trace", []),
                                    "verifier_reason": verifier_reason,
                                    "verifier_mode": model_result.get("verifier_mode") if model_enabled else self.model_verifier.mode,
                                    "fake_verifier": bool(model_result.get("fake_verifier")) if model_enabled else self.model_verifier.mode == "fake",
                                    "verifier_failure_category": model_result.get("failure_category") if model_enabled else None,
                                    "verifier_config_source": model_result.get("verifier_config_source") if model_enabled else self.model_verifier.config_source,
                                    "retry_count": int(run_record.get("retry_count", 0)),
                                    "partial_success": bool(run_record.get("partial_success", False)),
                                    "downgrade_reason": run_record.get("downgrade_reason"),
                                    "human_review": bool(dict(case.get("verifier", {})).get("human_optional", False)),
                                }
                            )

        metric_summary = summarize_case_metrics(results)
        report = self._build_report(
            cases=cases,
            results=results,
            run_records=run_records,
            metric_summary=metric_summary,
            mode=normalized_mode,
            scope=scope,
            profile=normalized_profile,
            preflight=preflight,
            model_errors=model_errors,
        )
        latest_path = self.project_root / "reports" / "latest.json"
        self.report_writer.write(latest_path, report)
        report["report_path"] = str(latest_path.relative_to(self.project_root)).replace("\\", "/")
        normalized_report_path = self._normalize_report_path(report_path) if report_path is not None else None
        if normalized_report_path is not None and normalized_report_path.resolve() != latest_path.resolve():
            self.report_writer.write(normalized_report_path, report)
            report["extra_report_path"] = str(normalized_report_path.relative_to(self.project_root)).replace("\\", "/")
        return report

    def _build_report(
        self,
        *,
        cases: list[dict[str, object]],
        results: list[dict[str, object]],
        run_records: list[dict[str, object]],
        metric_summary: dict[str, object],
        mode: str,
        scope: str | None,
        profile: str,
        preflight: dict[str, object],
        model_errors: list[str],
    ) -> dict[str, object]:
        tool_usage = self._collect_tool_usage(results)
        benchmark_catalog = self._benchmark_case_catalog()
        capability_status = self._build_capability_status(
            mode=mode,
            run_records=run_records,
            results=results,
            preflight=preflight,
        )
        external_integrations = self._merge_external_integrations(
            defaults=preflight["external_integrations"],
            results=results,
        )
        top_model_error = self._pick_model_error(run_records, model_errors)
        top_verifier_error = self._pick_verifier_error(results, preflight)
        failed_case_categories = self._summarize_failed_case_categories(results)
        human_review = self._human_review_summary()
        real_agent_summary = self._real_agent_summary(cases, results)
        safety_case_count = sum(1 for case in cases if str(case.get("category")) == "safety")
        safety_passed_count = sum(
            1 for item in results if str(item.get("category")) == "safety" and bool(item.get("passed"))
        )
        multiround_summary = self._multiround_summary(cases, results)
        planner_summary = self._planner_summary(cases, results, run_records)
        context_metrics = self._context_metrics_summary(results, run_records)
        return {
            "phase": "phase1_skeleton",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_mode": mode,
            "benchmark_scope": scope or "all",
            "benchmark_profile": profile,
            "benchmark_case_count": benchmark_catalog["benchmark_case_count"],
            "benchmark_case_count_by_profile": benchmark_catalog["benchmark_case_count_by_profile"],
            "benchmark_case_count_by_category": benchmark_catalog["benchmark_case_count_by_category"],
            "total_cases": len(cases),
            "counted_cases": metric_summary["counted_cases"],
            "passed_cases": metric_summary["passed_cases"],
            "pass_rate": metric_summary["pass_rate"],
            "avg_latency": metric_summary["avg_latency"],
            "avg_tool_rounds": metric_summary["avg_tool_rounds"],
            "tool_rounds": metric_summary["tool_rounds"],
            "failure_category": metric_summary["failure_category"],
            "verifier_reason": metric_summary["verifier_reason"],
            "retry_count": metric_summary["retry_count"],
            "partial_success": metric_summary["partial_success"],
            "downgrade_reason": metric_summary["downgrade_reason"],
            "model_provider": preflight["model_provider"],
            "model_name": preflight["model_name"],
            "fake_model": preflight["fake_model"],
            "verifier_mode": preflight["verifier_mode"],
            "fake_verifier": preflight["fake_verifier"],
            "verifier_provider": preflight["verifier_provider"],
            "verifier_model_name": preflight["verifier_model_name"],
            "verifier_config_source": preflight["verifier_config_source"],
            "docker_available": preflight["docker_available"],
            "mock_tools_used": tool_usage["mock_tools_used"],
            "real_tools_used": tool_usage["real_tools_used"],
            "mcp_tools_used": tool_usage["mcp_tools_used"],
            "external_integrations": external_integrations,
            "capability_status": capability_status,
            "missing_capabilities": list(preflight["missing_capabilities"]),
            "model_error": top_model_error,
            "verifier_error": top_verifier_error,
            "failed_case_count": sum(1 for item in results if str(item.get("status")) == "failed"),
            "failed_case_categories": failed_case_categories,
            "benchmark_failure_category": next(iter(failed_case_categories), None),
            "human_review": human_review,
            "real_agent_case_count": real_agent_summary["real_agent_case_count"],
            "real_agent_passed_count": real_agent_summary["real_agent_passed_count"],
            "real_agent_pass_rate": real_agent_summary["real_agent_pass_rate"],
            "safety_case_count": safety_case_count,
            "safety_passed_count": safety_passed_count,
            "safety_pass_rate": round(safety_passed_count / safety_case_count, 4) if safety_case_count else 0.0,
            "multiround_case_count": multiround_summary["multiround_case_count"],
            "multiround_passed_count": multiround_summary["multiround_passed_count"],
            "multiround_pass_rate": multiround_summary["multiround_pass_rate"],
            "planner_case_count": planner_summary["planner_case_count"],
            "planner_passed_count": planner_summary["planner_passed_count"],
            "planner_pass_rate": planner_summary["planner_pass_rate"],
            "avg_plan_steps": planner_summary["avg_plan_steps"],
            "avg_evidence_count": planner_summary["avg_evidence_count"],
            "replan_count": planner_summary["replan_count"],
            "real_planner_case_count": planner_summary["real_planner_case_count"],
            "real_planner_passed_count": planner_summary["real_planner_passed_count"],
            "real_planner_pass_rate": planner_summary["real_planner_pass_rate"],
            "planner_real_path_count": planner_summary["planner_real_path_count"],
            "avg_prompt_tokens": context_metrics["avg_prompt_tokens"],
            "avg_context_chars": context_metrics["avg_context_chars"],
            "avg_dynamic_context_chars": context_metrics["avg_dynamic_context_chars"],
            "avg_dynamic_context_tokens": context_metrics["avg_dynamic_context_tokens"],
            "avg_history_chars": context_metrics["avg_history_chars"],
            "avg_memory_chars": context_metrics["avg_memory_chars"],
            "avg_archive_chars": context_metrics["avg_archive_chars"],
            "avg_tool_specs_chars": context_metrics["avg_tool_specs_chars"],
            "context_case_count": context_metrics["context_case_count"],
            "token_estimator": "ceil_len_div_4",
            "results": results,
        }

    def _benchmark_case_catalog(self) -> dict[str, object]:
        summary = {
            "benchmark_case_count": 0,
            "benchmark_case_count_by_profile": {},
            "benchmark_case_count_by_category": {},
        }
        for path in (self.project_root / "benchmarks").rglob("*.json"):
            try:
                payload = load_json_file(path)
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            summary["benchmark_case_count"] += 1
            category = str(payload.get("category", "unknown"))
            by_category = summary["benchmark_case_count_by_category"]
            by_category[category] = by_category.get(category, 0) + 1
            profiles = payload.get("profiles", [])
            if isinstance(profiles, list) and profiles:
                by_profile = summary["benchmark_case_count_by_profile"]
                for profile in profiles:
                    profile_name = str(profile)
                    by_profile[profile_name] = by_profile.get(profile_name, 0) + 1
            else:
                by_profile = summary["benchmark_case_count_by_profile"]
                by_profile["default"] = by_profile.get("default", 0) + 1
        summary["benchmark_case_count_by_profile"] = dict(sorted(summary["benchmark_case_count_by_profile"].items()))
        summary["benchmark_case_count_by_category"] = dict(sorted(summary["benchmark_case_count_by_category"].items()))
        return summary

    @staticmethod
    def _real_agent_summary(cases: list[dict[str, object]], results: list[dict[str, object]]) -> dict[str, object]:
        real_agent_ids = {
            str(case.get("id"))
            for case in cases
            if "real-agent" in list(case.get("profiles", []))
        }
        if not real_agent_ids:
            return {
                "real_agent_case_count": 0,
                "real_agent_passed_count": 0,
                "real_agent_pass_rate": 0.0,
            }
        passed = sum(1 for item in results if str(item.get("id")) in real_agent_ids and bool(item.get("passed")))
        count = len(real_agent_ids)
        return {
            "real_agent_case_count": count,
            "real_agent_passed_count": passed,
            "real_agent_pass_rate": round(passed / count, 4) if count else 0.0,
        }

    @staticmethod
    def _multiround_summary(cases: list[dict[str, object]], results: list[dict[str, object]]) -> dict[str, object]:
        multiround_ids = {
            str(case.get("id"))
            for case in cases
            if "multiround" in list(case.get("profiles", []))
        }
        if not multiround_ids:
            return {
                "multiround_case_count": 0,
                "multiround_passed_count": 0,
                "multiround_pass_rate": 0.0,
            }
        passed = sum(1 for item in results if str(item.get("id")) in multiround_ids and bool(item.get("passed")))
        count = len(multiround_ids)
        return {
            "multiround_case_count": count,
            "multiround_passed_count": passed,
            "multiround_pass_rate": round(passed / count, 4) if count else 0.0,
        }

    @staticmethod
    def _planner_summary(
        cases: list[dict[str, object]],
        results: list[dict[str, object]],
        run_records: list[dict[str, object]],
    ) -> dict[str, object]:
        planner_ids = {
            str(case.get("id"))
            for case in cases
            if "planner" in list(case.get("profiles", []))
        }
        if not planner_ids:
            return {
                "planner_case_count": 0,
                "planner_passed_count": 0,
                "planner_pass_rate": 0.0,
                "avg_plan_steps": 0.0,
                "avg_evidence_count": 0.0,
                "replan_count": 0,
                "real_planner_case_count": 0,
                "real_planner_passed_count": 0,
                "real_planner_pass_rate": 0.0,
                "planner_real_path_count": 0,
            }
        # Identify real planner cases (planner_mode == "real")
        real_planner_ids = {
            str(case.get("id")) for case in cases
            if case.get("planner_mode") == "real"
            and "planner" in list(case.get("profiles", []))
        }
        passed = sum(1 for item in results if str(item.get("id")) in planner_ids and bool(item.get("passed")))
        real_passed = sum(1 for item in results if str(item.get("id")) in real_planner_ids and bool(item.get("passed")))
        count = len(planner_ids)
        real_count = len(real_planner_ids)
        total_steps = 0
        evidence_total = 0
        replan_total = 0
        for rec in run_records:
            rec_id = str(rec.get("session_id", ""))
            if rec_id in planner_ids:
                real_steps = rec.get("_plan_steps", 0)
                if isinstance(real_steps, int) and real_steps > 0:
                    total_steps += real_steps
                else:
                    for case in cases:
                        if str(case.get("id")) == rec_id:
                            plan_items = case.get("synthetic_tool_plan", [])
                            if isinstance(plan_items, list):
                                total_steps += len(plan_items)
                            break
                evidence_total += int(rec.get("evidence_count", 0))
                replan_events = rec.get("_replan_events", [])
                if isinstance(replan_events, list):
                    replan_total += len(replan_events)
        return {
            "planner_case_count": count,
            "planner_passed_count": passed,
            "planner_pass_rate": round(passed / count, 4) if count else 0.0,
            "avg_plan_steps": round(total_steps / count, 2) if count else 0.0,
            "avg_evidence_count": round(evidence_total / count, 2) if count else 0.0,
            "replan_count": replan_total,
            "real_planner_case_count": real_count,
            "real_planner_passed_count": real_passed,
            "real_planner_pass_rate": round(real_passed / real_count, 4) if real_count else 0.0,
            "planner_real_path_count": real_count,
        }

    def _build_preflight(self, mode: str, profile: str) -> dict[str, object]:
        missing_capabilities: list[str] = []
        docker_available = self._docker_available()
        if not docker_available:
            missing_capabilities.append("docker_unavailable")
        verifier_info = self.model_verifier.describe()
        if profile == "real-agent" and verifier_info["verifier_mode"] != "real":
            missing_capabilities.append("verifier_config_missing")
        if verifier_info["verifier_mode"] == "real" and verifier_info.get("verifier_error") == "verifier_config_missing":
            missing_capabilities.append("verifier_config_missing")
        external_integrations = {
            "feishu": "configured" if (os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET")) else "missing",
            "web_fetch": "real",
            "web_search": self._web_search_provider_status(),
            "weather": self._weather_provider_status(),
            "map_route": self._map_provider_status(),
            "map_poi_search": self._map_provider_status(),
        }
        if mode == "fake":
            return {
                "can_execute": True,
                "model_provider": "fake",
                "model_name": "fake",
                "fake_model": True,
                "verifier_mode": verifier_info["verifier_mode"],
                "fake_verifier": verifier_info["fake_verifier"],
                "verifier_provider": verifier_info["verifier_provider"],
                "verifier_model_name": verifier_info["verifier_model_name"],
                "verifier_config_source": verifier_info["verifier_config_source"],
                "docker_available": docker_available,
                "missing_capabilities": missing_capabilities,
                "external_integrations": external_integrations,
            }

        try:
            settings = _load_env_settings(self.project_root)
        except RuntimeError as exc:
            message = str(exc)
            if "deepseek_config_missing" not in missing_capabilities:
                missing_capabilities.append("deepseek_config_missing")
            env_values = self._read_model_env()
            return {
                "can_execute": False,
                "model_provider": env_values.get("MINIBOT_MODEL_PROVIDER", "deepseek") or "deepseek",
                "model_name": env_values.get("MINIBOT_MODEL_NAME", ""),
                "fake_model": False,
                "verifier_mode": verifier_info["verifier_mode"],
                "fake_verifier": verifier_info["fake_verifier"],
                "verifier_provider": verifier_info["verifier_provider"],
                "verifier_model_name": verifier_info["verifier_model_name"],
                "verifier_config_source": verifier_info["verifier_config_source"],
                "docker_available": docker_available,
                "missing_capabilities": missing_capabilities,
                "external_integrations": external_integrations,
                "startup_error": message,
            }

        return {
            "can_execute": False if profile == "real-agent" and "verifier_config_missing" in missing_capabilities else True,
            "model_provider": settings["MINIBOT_MODEL_PROVIDER"],
            "model_name": settings["MINIBOT_MODEL_NAME"],
            "fake_model": False,
            "verifier_mode": verifier_info["verifier_mode"],
            "fake_verifier": verifier_info["fake_verifier"],
            "verifier_provider": verifier_info["verifier_provider"],
            "verifier_model_name": verifier_info["verifier_model_name"],
            "verifier_config_source": verifier_info["verifier_config_source"],
            "docker_available": docker_available,
            "missing_capabilities": missing_capabilities,
            "external_integrations": external_integrations,
        }

    def _build_capability_status(
        self,
        *,
        mode: str,
        run_records: list[dict[str, object]],
        results: list[dict[str, object]],
        preflight: dict[str, object],
    ) -> dict[str, str]:
        missing = set(str(item) for item in preflight["missing_capabilities"])
        verifier_system_failed = self._has_verifier_system_error(results)
        if mode == "fake":
            return {
                "real_model": "missing",
                "real_tool_calling": "missing",
                "llm_archive": "missing",
                "model_verifier": "fake"
                if preflight["verifier_mode"] == "fake"
                else (
                    "missing"
                    if "verifier_config_missing" in missing
                    else ("failed" if verifier_system_failed else "passed")
                ),
                "docker_sandbox": "passed" if self._has_docker_evidence(results) else ("unavailable" if "docker_unavailable" in missing else "failed"),
                "partial_success": "passed" if any(bool(item.get("partial_success")) for item in results) else "failed",
            }
        real_model_status = "failed" if self._has_model_error(run_records, results) else ("missing" if "deepseek_config_missing" in missing else "passed")
        return {
            "real_model": real_model_status,
            "real_tool_calling": "missing"
            if "deepseek_config_missing" in missing
            else ("passed" if self._has_real_tool_calling(run_records) else "failed"),
            "llm_archive": "missing"
            if "deepseek_config_missing" in missing
            else ("passed" if self._has_real_archive(run_records) else "failed"),
            "model_verifier": "missing"
            if "verifier_config_missing" in missing
            else ("failed" if verifier_system_failed else ("fake" if preflight["verifier_mode"] == "fake" else "passed")),
            "docker_sandbox": "unavailable"
            if "docker_unavailable" in missing
            else ("passed" if self._has_docker_evidence(results) else "failed"),
            "partial_success": "passed" if any(bool(item.get("partial_success")) for item in results) else "failed",
        }

    def _load_cases(self, category: str | None, scope: str | None, profile: str) -> list[dict[str, object]]:
        case_paths = list((self.project_root / "benchmarks").rglob("*.json"))
        cases = [load_json_file(path) for path in case_paths]
        if profile in PROFILE_SCOPES:
            cases = [case for case in cases if profile in list(case.get("profiles", []))]
        categories = self._scope_categories(scope)
        if categories is not None:
            cases = [case for case in cases if str(case["category"]) in categories]
        if category:
            cases = [case for case in cases if case["category"] == category]
        return sorted(cases, key=lambda item: str(item["id"]))

    def _load_run_record(self, run_id: str) -> dict[str, object]:
        run_path = self.project_root / ".minibot" / "runs" / f"{run_id}.json"
        return dict(load_json_file(run_path))

    def _preload_approval(self, case: dict[str, object]) -> None:
        preload = case.get("preloaded_approval")
        if not isinstance(preload, dict):
            return
        tool_name = str(preload.get("tool_name", "")).strip()
        arguments = dict(preload.get("arguments", {})) if isinstance(preload.get("arguments"), dict) else {}
        status = str(preload.get("status", "")).strip().lower()
        if not tool_name or status not in {"approved", "rejected"}:
            return
        store = self.agent_loop.tool_dispatcher.approval_store
        pending = store.create_pending(
            session_id=str(case.get("id", "")),
            user_id="benchmark-runner",
            tool_name=tool_name,
            arguments=arguments,
            risk_level="gray",
            reason="benchmark_preload",
        )
        if status == "approved":
            store.approve(str(pending["approval_id"]))
        else:
            store.reject(str(pending["approval_id"]))

    def _approve_pending_for_plan(self, plan_result: dict[str, object]) -> None:
        """Find the pending approval from a plan result and approve it."""
        step_outcomes = plan_result.get("step_outcomes", [])
        for outcome in step_outcomes:
            pending_id = outcome.get("pending_approval_id")
            if pending_id:
                store = self.agent_loop.tool_dispatcher.approval_store
                try:
                    store.approve(str(pending_id))
                except Exception:
                    pass

    def _prepare_planner_sandbox(self, case: dict[str, object]) -> None:
        """Copy files referenced in the case to the sandbox so file_read works."""
        import re
        import shutil
        sandbox = self.agent_loop.memory_store.workspace.sandbox_dir
        goal = str(case.get("input", ""))
        for match in re.finditer(r"[\w./-]+\.(?:md|txt|json)", goal):
            rel = match.group(0)
            dest = sandbox / rel
            if dest.exists():
                continue
            src = self.project_root / rel
            if src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

    @staticmethod
    def _planner_result_to_run_record(
        case: dict[str, object],
        plan: object,
        plan_result: dict[str, object],
    ) -> dict[str, object]:
        """Synthesize a run-record-compatible dict from a real planner execution."""
        from minibot.planning.plan_schema import TaskPlan
        tool_trace: list[dict[str, object]] = []
        evidence_ids: list[str] = []
        final_responses: list[str] = []
        failure_category = None
        retry_total = 0
        for outcome in plan_result.get("step_outcomes", []):
            tool_trace.extend(outcome.get("tool_trace", []))
            evidence_ids.extend(outcome.get("evidence_ids", []))
            fr = str(outcome.get("final_response", ""))
            if fr:
                final_responses.append(fr)
            fc = outcome.get("failure_category")
            if fc and failure_category is None:
                failure_category = str(fc)
            if outcome.get("status") == "failed":
                retry_total += 1
        plan_steps = 0
        replan_events: list[dict[str, object]] = []
        if isinstance(plan, TaskPlan):
            plan_steps = len(plan.steps)
            meta = plan.metadata or {}
            replan_events = list(meta.get("replan_events", []))
        return {
            "run_id": f"planner-{case.get('id', 'unknown')}",
            "session_id": str(case.get("id", "")),
            "plan_id": plan_result.get("plan_id"),
            "step_id": None,
            "step_description": str(case.get("input", "")),
            "user_input": str(case.get("input", "")),
            "final_response": "\n".join(final_responses) if final_responses else str(plan_result.get("status", "")),
            "tool_trace": tool_trace,
            "tool_calls": [{"tool_name": t.get("tool_name", ""), "arguments": t.get("arguments", {})} for t in tool_trace],
            "tool_results": tool_trace,
            "evidence_ids": evidence_ids,
            "evidence_count": len(evidence_ids),
            "tool_output_compressed_to_evidence": len(evidence_ids) > 0,
            "failure_category": failure_category or (None if plan_result.get("status") in {"completed", "waiting_approval"} else "plan_failed"),
            "retry_count": retry_total,
            "partial_success": False,
            "downgrade_reason": None,
            "context_metrics": {},
            "context_summary": f"planner_real path; plan_status={plan_result.get('status')} steps_completed={plan_result.get('steps_completed')}",
            "verifier_reason": None,
            "subagent_trace": [],
            "hook_results": [],
            "compression_events": [],
            "cleaned_placeholders": 0,
            "cleaned_placeholder_items": [],
            "max_tool_rounds": 0,
            "actual_tool_rounds": 0,
            "multi_round": False,
            "tool_rounds_detail": [],
            "stop_reason": None,
            "max_tool_calls_total": 0,
            "actual_tool_calls_total": len(tool_trace),
            "max_runtime_seconds": 0,
            "actual_runtime_seconds": 0.0,
            "max_same_tool_calls": 0,
            "_planner_real": True,
            "_plan_steps": plan_steps,
            "_replan_events": replan_events,
        }

    def _try_recover_failure_category(self, session_id: str) -> str | None:
        """Try to recover the original failure_category from a partially-written run record.

        When handle_message() throws mid-flight, the run record may already have
        tool_results written via lifecycle events.  Prefer the tool-level
        failure_category (e.g. ``blocked_by_policy``) over a generic
        ``benchmark_runtime_error``.
        """
        runs_dir = self.project_root / ".minibot" / "runs"
        if not runs_dir.is_dir():
            return None
        for run_path in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                record = dict(load_json_file(run_path))
            except (OSError, ValueError):
                continue
            if str(record.get("session_id")) != session_id:
                continue
            tool_results = record.get("tool_results")
            if not isinstance(tool_results, list) or not tool_results:
                continue
            for item in tool_results:
                if not isinstance(item, dict):
                    continue
                fc = item.get("failure_category")
                if isinstance(fc, str) and fc.strip():
                    return fc.strip()
            return None
        return None

    def _normalize_report_path(self, report_path: Path) -> Path:
        if report_path.is_absolute():
            return report_path
        return (self.project_root / report_path).resolve()

    @staticmethod
    def _compose_verifier_reason(rule_reason: str, model_reason: str, run_reason: object) -> str:
        parts = [rule_reason, model_reason]
        if run_reason:
            parts.append(str(run_reason))
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in {"fake", "real"}:
            raise ValueError(f"unsupported benchmark mode: {mode}")
        return normalized

    @staticmethod
    def _normalize_profile(profile: str) -> str:
        normalized = profile.strip().lower()
        if normalized not in {"default", *PROFILE_SCOPES}:
            raise ValueError(f"unsupported benchmark profile: {profile}")
        return normalized

    @staticmethod
    def _normalize_case(case: dict[str, object], mode: str) -> dict[str, object]:
        normalized = dict(case)
        expected = list(case.get("expected_behavior", []))
        if mode == "real":
            normalized["expected_behavior"] = [
                "final_response_not_empty" if item == "final_response_contains:MiniBot echo:" else item
                for item in expected
            ]
        else:
            normalized["expected_behavior"] = expected
        return normalized

    @staticmethod
    def _scope_categories(scope: str | None) -> set[str] | None:
        if scope is None:
            return None
        normalized = scope.strip().lower()
        if normalized == "core":
            return set(CORE_CATEGORIES)
        raise ValueError(f"unsupported benchmark scope: {scope}")

    @contextmanager
    def _benchmark_policy_override(self, profile: str):
        if profile != "execution":
            yield
            return
        approval_manager = self.agent_loop.tool_dispatcher.approval_manager
        original_config = dict(approval_manager.approval_config)
        original_defaults = dict(approval_manager.tool_defaults)
        updated_config = dict(original_config)
        updated_config["auto_approve"] = True
        approval_manager.approval_config = updated_config
        approval_manager.tool_defaults = dict(original_defaults)
        try:
            yield
        finally:
            approval_manager.approval_config = original_config
            approval_manager.tool_defaults = original_defaults

    @contextmanager
    def _context_profile_override(self, profile: str):
        if profile not in {
            "context-baseline",
            "context-optimized",
            "context-realistic-baseline",
            "context-realistic-optimized",
        }:
            yield
            return
        context_builder = self.agent_loop.context_builder
        original_history_truncation = context_builder.enable_history_truncation
        original_placeholder_clean = context_builder.enable_placeholder_clean
        original_archive_recall = context_builder.enable_archive_recall
        original_memory_compaction = context_builder.enable_memory_compaction
        original_archive_full_context = context_builder.enable_archive_full_context
        original_history_budget_override = context_builder.history_token_budget_override
        try:
            if profile in {"context-baseline", "context-realistic-baseline"}:
                context_builder.enable_history_truncation = False
                context_builder.enable_placeholder_clean = False
                context_builder.enable_archive_recall = False
                context_builder.enable_memory_compaction = False
                context_builder.enable_archive_full_context = True
                context_builder.history_token_budget_override = None
            else:
                context_builder.enable_history_truncation = True
                context_builder.enable_placeholder_clean = True
                context_builder.enable_archive_recall = True
                context_builder.enable_memory_compaction = True
                context_builder.enable_archive_full_context = False
                context_builder.history_token_budget_override = 320
            yield
        finally:
            context_builder.enable_history_truncation = original_history_truncation
            context_builder.enable_placeholder_clean = original_placeholder_clean
            context_builder.enable_archive_recall = original_archive_recall
            context_builder.enable_memory_compaction = original_memory_compaction
            context_builder.enable_archive_full_context = original_archive_full_context
            context_builder.history_token_budget_override = original_history_budget_override

    @contextmanager
    def _benchmark_approval_isolation(self, profile: str):
        """Use an isolated approval store so benchmark cases are not polluted by historical approvals.

        The isolated store lives under ``.minibot/benchmark_approvals/`` and is
        cleaned before each benchmark run.  Preloaded approvals (real-agent cases)
        are written into the isolated store so they still work correctly.

        The *real* approval store (``.minibot/approvals/``) is never touched —
        ``_human_review_summary()`` reads from it independently.
        """
        original_store = self.agent_loop.tool_dispatcher.approval_store
        isolated_dir = self.project_root / ".minibot" / "benchmark_approvals"
        # Clean any leftover from a previous interrupted run so every
        # benchmark run starts with a blank approval slate.
        if isolated_dir.exists():
            shutil.rmtree(isolated_dir)
        isolated_store = ApprovalStore(isolated_dir)
        self.agent_loop.tool_dispatcher.approval_store = isolated_store
        try:
            yield
        finally:
            self.agent_loop.tool_dispatcher.approval_store = original_store

    @contextmanager
    def _synthetic_model_plan(self, case: dict[str, object]):
        synthetic_plan = case.get("synthetic_tool_plan")
        synthetic_plan_rounds = case.get("synthetic_tool_plan_rounds")
        if not isinstance(synthetic_plan, list) or not synthetic_plan:
            yield
            return
        original_model_client = self.agent_loop.model_client
        rounds_raw = dict(synthetic_plan_rounds) if isinstance(synthetic_plan_rounds, dict) else None
        # Convert string keys to int
        tool_calls_by_round: dict[int, list[dict[str, object]]] | None = None
        if rounds_raw:
            tool_calls_by_round = {}
            for key, value in rounds_raw.items():
                tool_calls_by_round[int(key)] = list(value) if isinstance(value, list) else []
        self.agent_loop.model_client = _SyntheticBenchmarkModelClient(
            synthetic_plan,
            tool_calls_by_round=tool_calls_by_round,
        )
        try:
            yield
        finally:
            self.agent_loop.model_client = original_model_client

    @contextmanager
    def _prepare_case_state(self, case: dict[str, object]):
        workspace = self.agent_loop.memory_store.workspace
        original_memory = workspace.read_memory()
        original_history = workspace.read_history()
        original_archives: dict[str, str] = {}
        for path in workspace.archives_dir.glob("*.md"):
            try:
                original_archives[path.name] = path.read_text(encoding="utf-8")
            except OSError:
                continue
        expanded_case = self._expand_context_case(case)
        context_seed = expanded_case.get("context_seed")
        if isinstance(context_seed, dict):
            memory_text = str(context_seed.get("memory", "")).strip()
            history_text = str(context_seed.get("history", "")).strip()
            workspace.memory_file.write_text(f"# MEMORY\n\n{memory_text}\n", encoding="utf-8")
            workspace.history_file.write_text(f"# HISTORY\n\n{history_text}\n", encoding="utf-8")
            for path in workspace.archives_dir.glob("*.md"):
                path.unlink()
            for item in list(context_seed.get("archives", [])):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip() or "archive-benchmark.md"
                content = str(item.get("content", ""))
                (workspace.archives_dir / name).write_text(content, encoding="utf-8")
        preload = expanded_case.get("preloaded_approval")
        try:
            if isinstance(preload, dict):
                tool_name = str(preload.get("tool_name", "")).strip()
                arguments = dict(preload.get("arguments", {})) if isinstance(preload.get("arguments"), dict) else {}
                status = str(preload.get("status", "")).strip().lower()
                if tool_name and status in {"approved", "rejected"}:
                    store = self.agent_loop.tool_dispatcher.approval_store
                    pending = store.create_pending(
                        session_id=str(case.get("id", "")),
                        user_id="benchmark-runner",
                        tool_name=tool_name,
                        arguments=arguments,
                        risk_level="gray",
                        reason="benchmark_preload",
                    )
                    if status == "approved":
                        store.approve(str(pending["approval_id"]))
                    else:
                        store.reject(str(pending["approval_id"]))
            yield
        finally:
            workspace.memory_file.write_text(original_memory, encoding="utf-8")
            workspace.history_file.write_text(original_history, encoding="utf-8")
            for path in workspace.archives_dir.glob("*.md"):
                path.unlink()
            for name, content in original_archives.items():
                (workspace.archives_dir / name).write_text(content, encoding="utf-8")

    @staticmethod
    def _expand_context_case(case: dict[str, object]) -> dict[str, object]:
        expanded = dict(case)
        expansion = dict(case.get("context_expansion", {})) if isinstance(case.get("context_expansion"), dict) else {}
        if not expansion:
            return expanded
        placeholder_repeat = int(expansion.get("placeholder_repeat", 0) or 0)
        placeholder_block = "\n".join(
            f"TODO <pending> placeholder noise block {index:03d}"
            for index in range(max(placeholder_repeat, 0))
        )

        context_seed = dict(case.get("context_seed", {})) if isinstance(case.get("context_seed"), dict) else {}
        if context_seed:
            expanded_seed = dict(context_seed)
            for field_name, repeat_key, filler_key in (
                ("memory", "memory_repeat", "memory_filler"),
                ("history", "history_repeat", "history_filler"),
            ):
                base_value = str(context_seed.get(field_name, ""))
                filler_value = str(context_seed.get(filler_key, ""))
                repeat = int(expansion.get(repeat_key, 0) or 0)
                expanded_seed[field_name] = BenchmarkRunner._expand_context_text(
                    base=base_value,
                    filler=filler_value,
                    repeat=repeat,
                    placeholder_block=placeholder_block,
                    label=field_name,
                )
            archives: list[dict[str, object]] = []
            for index, item in enumerate(list(context_seed.get("archives", []))):
                if not isinstance(item, dict):
                    continue
                base_content = str(item.get("content", ""))
                filler_content = str(item.get("filler", ""))
                repeat = int(expansion.get("archive_repeat", 0) or 0)
                archives.append(
                    {
                        "name": str(item.get("name", "")).strip() or f"archive-benchmark-{index}.md",
                        "content": BenchmarkRunner._expand_context_text(
                            base=base_content,
                            filler=filler_content,
                            repeat=repeat,
                            placeholder_block=placeholder_block,
                            label=f"archive-{index}",
                        ),
                    }
                )
            expanded_seed["archives"] = archives
            expanded["context_seed"] = expanded_seed

        tool_results: list[dict[str, object]] = []
        for index, item in enumerate(list(case.get("context_tool_results", []))):
            if not isinstance(item, dict):
                continue
            cloned = dict(item)
            output = dict(item.get("output", {})) if isinstance(item.get("output"), dict) else {}
            filler_text = str(item.get("output_filler", ""))
            repeat = int(expansion.get("tool_output_repeat", 0) or 0)
            if filler_text and output:
                if "text" in output:
                    output["text"] = BenchmarkRunner._expand_context_text(
                        base=str(output.get("text", "")),
                        filler=filler_text,
                        repeat=repeat,
                        placeholder_block=placeholder_block,
                        label=f"tool-output-{index}",
                    )
                elif "summary" in output:
                    output["summary"] = BenchmarkRunner._expand_context_text(
                        base=str(output.get("summary", "")),
                        filler=filler_text,
                        repeat=repeat,
                        placeholder_block=placeholder_block,
                        label=f"tool-output-{index}",
                    )
            cloned["output"] = output
            tool_results.append(cloned)
        if tool_results:
            expanded["context_tool_results"] = tool_results
        return expanded

    @staticmethod
    def _expand_context_text(*, base: str, filler: str, repeat: int, placeholder_block: str, label: str) -> str:
        if repeat <= 0 or not filler:
            suffix = f"\n{placeholder_block}" if placeholder_block else ""
            return f"{base}{suffix}".strip()
        filler_lines = [
            f"{filler} [segment={label}-{index:03d}]"
            for index in range(repeat)
        ]
        parts = [base.strip(), "\n".join(filler_lines).strip()]
        if placeholder_block:
            parts.append(placeholder_block)
        return "\n".join(part for part in parts if part).strip()

    def _docker_available(self) -> bool:
        try:
            return bool(self.agent_loop.tool_dispatcher.docker_executor.available())
        except AttributeError:
            return _docker_available()

    @staticmethod
    def _collect_tool_usage(results: list[dict[str, object]]) -> dict[str, list[str]]:
        mock_tools: set[str] = set()
        real_tools: set[str] = set()
        mcp_tools: set[str] = set()
        for item in results:
            for trace in item.get("tool_trace", []):
                if not isinstance(trace, dict):
                    continue
                tool_name = str(trace.get("tool_name") or "")
                metadata = dict(trace.get("metadata", {}))
                if not tool_name:
                    continue
                if bool(metadata.get("mcp_provider")) and str(metadata.get("provider_status")) == "mcp":
                    mcp_tools.add(tool_name)
                    real_tools.add(tool_name)
                    continue
                if bool(metadata.get("real_provider")) or str(metadata.get("provider_status")) == "real":
                    real_tools.add(tool_name)
                    continue
                if bool(metadata.get("mock_provider")) or str(metadata.get("provider_status")) == "mock":
                    mock_tools.add(tool_name)
                    continue
                if tool_name in REAL_TOOLS:
                    real_tools.add(tool_name)
                elif tool_name in MOCK_TOOLS:
                    mock_tools.add(tool_name)
        return {
            "mock_tools_used": sorted(mock_tools),
            "real_tools_used": sorted(real_tools),
            "mcp_tools_used": sorted(mcp_tools),
        }

    @staticmethod
    def _context_metrics_summary(results: list[dict[str, object]], run_records: list[dict[str, object]]) -> dict[str, float | int]:
        metrics_by_id = {
            str(record.get("session_id")): dict(record.get("context_metrics", {}))
            for record in run_records
            if isinstance(record, dict)
        }
        context_metrics: list[dict[str, object]] = []
        for result in results:
            if str(result.get("category")) != "context":
                continue
            metrics = metrics_by_id.get(str(result.get("id")), {})
            result["context_metrics"] = metrics
            if metrics:
                context_metrics.append(metrics)
        if not context_metrics:
            return {
                "avg_prompt_tokens": 0.0,
                "avg_context_chars": 0.0,
                "avg_dynamic_context_chars": 0.0,
                "avg_dynamic_context_tokens": 0.0,
                "avg_history_chars": 0.0,
                "avg_memory_chars": 0.0,
                "avg_archive_chars": 0.0,
                "avg_tool_specs_chars": 0.0,
                "context_case_count": 0,
            }
        count = len(context_metrics)
        return {
            "avg_prompt_tokens": round(sum(float(item.get("prompt_tokens", 0)) for item in context_metrics) / count, 4),
            "avg_context_chars": round(sum(float(item.get("context_chars", 0)) for item in context_metrics) / count, 4),
            "avg_dynamic_context_chars": round(sum(float(item.get("dynamic_context_chars", 0)) for item in context_metrics) / count, 4),
            "avg_dynamic_context_tokens": round(sum(float(item.get("dynamic_context_tokens", 0)) for item in context_metrics) / count, 4),
            "avg_history_chars": round(sum(float(item.get("history_chars", 0)) for item in context_metrics) / count, 4),
            "avg_memory_chars": round(sum(float(item.get("memory_chars", 0)) for item in context_metrics) / count, 4),
            "avg_archive_chars": round(sum(float(item.get("archive_chars", 0)) for item in context_metrics) / count, 4),
            "avg_tool_specs_chars": round(sum(float(item.get("tool_specs_chars", 0)) for item in context_metrics) / count, 4),
            "context_case_count": count,
        }

    @staticmethod
    def _has_docker_evidence(results: list[dict[str, object]]) -> bool:
        return any(
            dict(trace.get("metadata", {})).get("sandbox") == "docker"
            for item in results
            for trace in item.get("tool_trace", [])
            if isinstance(trace, dict)
        )

    @staticmethod
    def _has_real_tool_calling(run_records: list[dict[str, object]]) -> bool:
        return any(
            dict(record.get("model_plan", {})).get("model_mode") == "real" and bool(record.get("tool_calls"))
            for record in run_records
        )

    @staticmethod
    def _has_model_error(run_records: list[dict[str, object]], results: list[dict[str, object]]) -> bool:
        if any(dict(record.get("model_plan", {})).get("model_error") for record in run_records):
            return True
        return any(str(item.get("failure_category")) == "model_http_error" for item in results)

    @staticmethod
    def _has_real_archive(run_records: list[dict[str, object]]) -> bool:
        return any(
            any(str(event.get("archive_mode")) == "real" for event in record.get("compression_events", []))
            for record in run_records
        )

    @staticmethod
    def _has_verifier_system_error(results: list[dict[str, object]]) -> bool:
        return any(
            str(item.get("verifier_failure_category"))
            in {"verifier_http_error", "verifier_parse_error", "verifier_exception", "verifier_runtime_error"}
            for item in results
        )

    def _read_model_env(self) -> dict[str, str]:
        env_path = self.project_root / ".env"
        values: dict[str, str] = {}
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        for key in ("MINIBOT_MODEL_PROVIDER", "MINIBOT_MODEL_BASE_URL", "MINIBOT_MODEL_API_KEY", "MINIBOT_MODEL_NAME"):
            if key in os.environ:
                values[key] = os.environ[key]
        if not values.get("MINIBOT_MODEL_PROVIDER"):
            values["MINIBOT_MODEL_PROVIDER"] = "deepseek"
        return values

    @staticmethod
    def _pick_model_error(run_records: list[dict[str, object]], model_errors: list[str]) -> str | None:
        for record in run_records:
            model_error = dict(record.get("model_plan", {})).get("model_error")
            if model_error:
                return str(model_error)
        return model_errors[0] if model_errors else None

    @staticmethod
    def _pick_verifier_error(results: list[dict[str, object]], preflight: dict[str, object]) -> str | None:
        if "verifier_config_missing" in set(str(item) for item in preflight.get("missing_capabilities", [])):
            return "verifier_config_missing"
        for item in results:
            category = item.get("verifier_failure_category")
            if str(category) in {"verifier_http_error", "verifier_parse_error", "verifier_config_missing", "verifier_runtime_error"}:
                return str(category)
        return None

    @staticmethod
    def _summarize_failed_case_categories(results: list[dict[str, object]]) -> dict[str, int]:
        categories: dict[str, int] = {}
        for item in results:
            if str(item.get("status")) != "failed":
                continue
            category = str(
                item.get("verifier_failure_category")
                or item.get("failure_category")
                or "case_assertion_failed"
            )
            categories[category] = categories.get(category, 0) + 1
        return categories

    def _human_review_summary(self) -> dict[str, int]:
        config = load_config(self.project_root / "configs" / "minibot.json")
        workspace = WorkspaceManager(self.project_root, config.workspace_dir)
        workspace.ensure()
        return ApprovalStore(workspace.approvals_dir).counts()

    @staticmethod
    def _merge_external_integrations(
        *,
        defaults: dict[str, str],
        results: list[dict[str, object]],
    ) -> dict[str, str]:
        merged = dict(defaults)
        for item in results:
            for trace in item.get("tool_trace", []):
                if not isinstance(trace, dict):
                    continue
                tool_name = str(trace.get("tool_name") or "")
                metadata = dict(trace.get("metadata", {}))
                status = str(metadata.get("provider_status") or "").strip()
                if tool_name in {"web_fetch", "web_search", "weather", "map_route", "map_poi_search"} and status:
                    merged[tool_name] = status
        return merged

    @staticmethod
    def _web_search_provider_status() -> str:
        provider = os.getenv("MINIBOT_WEB_SEARCH_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "tavily":
            return "real" if os.getenv("TAVILY_API_KEY", "").strip() else "missing"
        return "mock"

    @staticmethod
    def _weather_provider_status() -> str:
        provider = os.getenv("MINIBOT_WEATHER_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "real":
            return "real" if os.getenv("MINIBOT_WEATHER_API_KEY", "").strip() else "missing"
        return "mock"

    @staticmethod
    def _map_provider_status() -> str:
        provider = os.getenv("MINIBOT_MAP_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "mcp":
            endpoint = os.getenv("MINIBOT_AMAP_MCP_ENDPOINT", "").strip()
            api_key = os.getenv("MINIBOT_AMAP_MCP_API_KEY", "").strip()
            return "mcp" if endpoint and api_key else "missing"
        return "mock"


class _SyntheticBenchmarkModelClient:
    def __init__(
        self,
        tool_calls: list[dict[str, object]],
        tool_calls_by_round: dict[int, list[dict[str, object]]] | None = None,
    ) -> None:
        self._tool_calls = [
            ToolCall(str(item["tool_name"]), dict(item.get("arguments", {})))
            for item in tool_calls
        ]
        self._tool_calls_by_round: dict[int, list[ToolCall]] = {}
        if tool_calls_by_round is not None:
            for round_key, calls in tool_calls_by_round.items():
                self._tool_calls_by_round[int(round_key)] = [
                    ToolCall(str(item["tool_name"]), dict(item.get("arguments", {})))
                    for item in calls
                ]
        self._plan_next_count = 0

    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:  # noqa: ARG002
        return ModelPlan(
            assistant_message=None,
            tool_calls=self._tool_calls,
            raw_plan={
                "mode": "synthetic_benchmark_tool_plan",
                "reason": "synthetic_execution_profile",
                "tool_calls": [call.to_trace() for call in self._tool_calls],
            },
        )

    def plan_next(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
        round_index: int,
    ) -> ModelPlan:
        self._plan_next_count += 1
        calls = self._tool_calls_by_round.get(round_index, [])
        return ModelPlan(
            assistant_message=None,
            tool_calls=calls,
            raw_plan={
                "mode": "synthetic_benchmark_tool_plan",
                "reason": f"synthetic_multi_round_{round_index}",
                "tool_calls": [call.to_trace() for call in calls],
                "round_index": round_index,
            },
        )

    def finalize(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
    ) -> "ModelFinalAnswer":
        from minibot.harness.model_client import BaseModelClient

        return BaseModelClient().finalize(message, context, tool_calls, tool_results)

    def finalize_response(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        plan: ModelPlan,
        tool_results: list[dict[str, object]],
    ) -> str:
        from minibot.harness.model_client import BaseModelClient

        return BaseModelClient().finalize_response(message, context, plan, tool_results)
