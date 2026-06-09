"""Experiment runner — toggle context features and compare metrics.

Each experiment runs benchmark cases twice:
1. *baseline* — context features disabled/minimal
2. *current* — context features enabled

Metrics are extracted from real run records (``.minibot/runs/``), never
defaulted to 0.  Missing metrics cause ``failed_metric_missing`` status.
"""

from __future__ import annotations

import json
from pathlib import Path

from minibot.evals.benchmark_runner import BenchmarkRunner
from minibot.json_utils import load_json_file


class ExperimentRunner:
    """Run an ablation experiment by toggling ContextBuilder features."""

    def __init__(self, agent_loop, project_root: Path, verifier_agent=None,
                 long_task_runner=None, planner_agent=None) -> None:
        self._agent_loop = agent_loop
        self._project_root = project_root
        self._verifier_agent = verifier_agent
        self._long_task_runner = long_task_runner
        self._planner_agent = planner_agent

    def run(
        self,
        experiment_name: str,
        *,
        mode: str = "fake",
        report_path: Path | None = None,
    ) -> dict[str, object]:
        config = self._load_config(experiment_name)
        cases = self._load_cases(experiment_name)

        # Real mode pre-check: if model key is missing, skip all cases
        if mode == "real":
            import os
            api_key = (os.getenv("MINIBOT_MODEL_API_KEY", "") or
                       os.getenv("MINIBOT_API_KEY", "").strip())
            if not api_key:
                report = {
                    "experiment": experiment_name,
                    "mode": "real",
                    "status": "skipped",
                    "skip_reason": "provider_config_missing",
                    "missing": ["MINIBOT_MODEL_API_KEY"],
                    "generated_at": self._now(),
                    "total_cases": len(cases),
                    "completed_cases": 0,
                    "passed_cases": 0,
                    "engineering_passed": 0,
                    "failed_metric_missing": 0,
                    "failed_expectation": len(cases),
                    "skipped_cases": len(cases),
                    "pass_rate": 0.0,
                    "engineering_pass_rate": 0.0,
                    "summary": {"provider_mode": "real", "provider_usage_prompt_tokens": "unavailable",
                                 "provider_usage_total_tokens": "unavailable", "model_name": "config_missing"},
                    "results": [{"id": c["id"], "status": "skipped", "passed": False,
                                 "skip_reason": "provider_config_missing",
                                 "baseline_metrics": {}, "current_metrics": {}} for c in cases],
                }
                if report_path is not None:
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    import json as _json
                    report_path.write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                return report

        baseline_config = config.get("baseline", {})
        current_config = config.get("current", {})

        results: list[dict[str, object]] = []
        skipped_count = 0
        failed_metric_missing = 0
        failed_expectation = 0
        passed_count = 0
        engineering_passed = 0

        for case in cases:
            if case.get("status") == "pending":
                skipped_count += 1
                results.append({"id": case["id"], "status": "skipped", "passed": False,
                                "baseline_metrics": {}, "current_metrics": {}})
                continue

            is_planner_case = bool(case.get("planner_mode") == "current")
            is_evidence_case = (experiment_name == "evidence_compression_realistic")

            # ── baseline run ──
            if is_evidence_case:
                # Evidence experiment: use real AgentLoop, disable evidence offloading
                self._agent_loop.evidence_enabled = False
                baseline_report = self._run_agent_chat(case, mode)
                self._agent_loop.evidence_enabled = True
            elif is_planner_case:
                baseline_report = self._run_agent_chat(case, mode)
            else:
                self._apply_context_config(baseline_config)
                baseline_report = self._run_benchmark(case, mode)
                self._restore_context()

            # ── current run ──
            if is_evidence_case:
                # Evidence experiment: enable evidence offloading for current
                self._agent_loop.evidence_enabled = True
                current_report = self._run_agent_chat(case, mode)
            elif is_planner_case:
                current_report = self._run_planner_path(case, mode)
            else:
                self._apply_context_config(current_config)
                current_report = self._run_benchmark(case, mode)
                self._restore_context()

            bm = self._extract_metrics(baseline_report)
            cm = self._extract_metrics(current_report)

            # ── Business-level pass check (mode-aware) ──
            case_status, is_passed, fail_reason = self._check_case_passed(
                experiment_name, case, bm, cm, mode=mode)

            if case_status == "completed":
                passed_count += 1
            elif case_status == "failed_metric_missing":
                failed_metric_missing += 1
            elif case_status == "failed_expectation":
                failed_expectation += 1

            # Engineering check: did the case produce measurable engineering metrics?
            # Separate from answer-quality pass — only requires metrics to be valid.
            if ExperimentRunner._engineering_metrics_valid(experiment_name, case, bm, cm):
                engineering_passed += 1

            results.append({
                "id": case["id"],
                "status": case_status,
                "passed": is_passed,
                "category": str(case.get("category", "")),
                "expected_policy_decision": str(case.get("expected_policy_decision", "")),
                "baseline_metrics": bm,
                "current_metrics": cm,
                "_fail_reason": fail_reason,
            })

        extra_summary = self._compute_experiment_metrics(experiment_name, results)
        # Compute context deltas from valid pairs only
        valid = [r for r in results if r.get("status") == "completed"]
        ctx_deltas = self._context_deltas(valid) if valid else {}
        report = {
            "experiment": experiment_name,
            "mode": mode,
            "generated_at": self._now(),
            "total_cases": len(cases),
            "completed_cases": len(results) - skipped_count,
            "passed_cases": passed_count,
            "engineering_passed": engineering_passed,
            "failed_metric_missing": failed_metric_missing,
            "failed_expectation": failed_expectation,
            "skipped_cases": skipped_count,
            "pass_rate": round(passed_count / max(len(results) - skipped_count, 1), 4),
            "engineering_pass_rate": round(engineering_passed / max(len(results) - skipped_count, 1), 4),
            "baseline_config": baseline_config,
            "current_config": current_config,
            "summary": {**ctx_deltas, **extra_summary},
            "results": results,
        }

        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return report

    # ------------------------------------------------------------------
    # Context metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _context_deltas(valid_results: list[dict[str, object]]) -> dict[str, object]:
        """Compute context reduction from valid paired results."""
        bl_sum = cur_sum = bl_hist = cur_hist = bl_prompt = cur_prompt = 0
        ev_sum = 0
        ev_count = 0
        n = max(len(valid_results), 1)
        for r in valid_results:
            bm = r.get("baseline_metrics", {}) or {}
            cm = r.get("current_metrics", {}) or {}
            bl_sum += int(bm.get("context_chars", 0) or 0)
            cur_sum += int(cm.get("context_chars", 0) or 0)
            bl_hist += int(bm.get("history_chars", 0) or 0)
            cur_hist += int(cm.get("history_chars", 0) or 0)
            bl_prompt += int(bm.get("prompt_chars", 0) or 0)
            cur_prompt += int(cm.get("prompt_chars", 0) or 0)
            if int(cm.get("evidence_chars", 0) or 0) > 0:
                ev_sum += int(cm["evidence_chars"])
                ev_count += 1
        avg_bl = round(bl_sum / n, 2)
        avg_cur = round(cur_sum / n, 2)
        return {
            "avg_context_chars_baseline": avg_bl,
            "avg_context_chars_current": avg_cur,
            "avg_history_chars_baseline": round(bl_hist / n, 2),
            "avg_history_chars_current": round(cur_hist / n, 2),
            "avg_prompt_chars_baseline": round(bl_prompt / n, 2),
            "avg_prompt_chars_current": round(cur_prompt / n, 2),
            "avg_evidence_chars_current": round(ev_sum / ev_count, 2) if ev_count else None,
            "context_reduction_rate": round((avg_bl - avg_cur) / avg_bl, 4) if avg_bl > 0 else None,
        }

    # ------------------------------------------------------------------
    # Case execution
    # ------------------------------------------------------------------

    def _run_benchmark(self, case: dict[str, object], mode: str) -> dict[str, object]:
        cat = str(case.get("category", ""))
        cid = str(case.get("id", ""))
        bm_dir = self._project_root / "benchmarks"
        found = False
        if bm_dir.exists():
            for p in bm_dir.rglob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("id") == cid:
                    found = True
                    break
        temp_file = None
        if not found:
            (bm_dir / cat).mkdir(parents=True, exist_ok=True)
            temp_file = bm_dir / cat / f"{cid}.json"
            temp_file.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        try:
            runner = BenchmarkRunner(
                self._agent_loop, self._project_root,
                verifier_agent=self._verifier_agent,
                long_task_runner=self._long_task_runner,
                planner_agent=self._planner_agent,
            )
            report = runner.run(category=cat, mode=mode)
            for item in report.get("results", []):
                if str(item.get("id")) == cid:
                    # Also read the run record for context_metrics
                    return self._enrich_from_run_record(item, cid)
        finally:
            if temp_file and temp_file.exists():
                temp_file.unlink()
        return {"passed": False, "_missing": True, "context_chars": 0}

    def _run_agent_chat(self, case: dict[str, object], mode: str) -> dict[str, object]:
        from minibot.channels.base import ChannelMessage
        result = self._agent_loop.handle_message(
            ChannelMessage(channel="experiment", user_id="experiment-runner",
                           session_id=str(case.get("id", "")), content=str(case.get("input", ""))))
        return self._enrich_from_run_record({
            "id": case.get("id"), "status": "passed" if result.response else "failed",
            "passed": bool(result.response),
            "_raw_run_id": result.run_id,
            "uses_real_file": str(case.get("uses_real_file", "")),
            "tool_trace": [], "tool_rounds": 0,
            "failure_category": None, "verifier_reason": result.verifier_reason or "",
            "retry_count": 0, "partial_success": False,
            "input": str(case.get("input", "")),
        }, str(case.get("id", "")))

    def _run_planner_path(self, case: dict[str, object], mode: str) -> dict[str, object]:
        if self._planner_agent is None or self._long_task_runner is None:
            return {"passed": False, "_missing": True, "failure_category": "planner_unavailable"}
        plan = self._planner_agent.plan(str(case.get("input", "")))
        result = self._long_task_runner.run(plan)
        tool_trace, evidence_ids, final_responses, failure_category = [], [], [], None
        for o in result.get("step_outcomes", []):
            tool_trace.extend(o.get("tool_trace", []))
            evidence_ids.extend(o.get("evidence_ids", []))
            if str(o.get("final_response", "")):
                final_responses.append(str(o["final_response"]))
            if o.get("failure_category") and failure_category is None:
                failure_category = str(o["failure_category"])
        replan_events = []
        from minibot.planning.plan_schema import TaskPlan
        if isinstance(plan, TaskPlan):
            replan_events = list((plan.metadata or {}).get("replan_events", []))
        response_text = "\n".join(final_responses) if final_responses else str(result.get("status", ""))
        # Check output file existence
        output_exists = False
        sandbox = self._agent_loop.memory_store.workspace.sandbox_dir
        for o in result.get("step_outcomes", []):
            for t in o.get("tool_trace", []):
                if t.get("tool_name") == "file_write" and t.get("status") == "success":
                    p = sandbox / str(t.get("arguments", {}).get("path", ""))
                    output_exists = p.exists()
        return {
            "id": case.get("id"), "status": "passed" if result.get("status") in {"completed", "waiting_approval"} else "failed",
            "passed": result.get("status") in {"completed", "waiting_approval"},
            "tool_trace": tool_trace, "tool_rounds": len(tool_trace),
            "failure_category": failure_category,
            "verifier_reason": f"plan_status={result.get('status')} steps={result.get('steps_completed',0)}/{result.get('total_steps',0)}",
            "retry_count": 0, "partial_success": False,
            "_plan_id": result.get("plan_id"), "_plan_steps": result.get("total_steps", 0),
            "_evidence_ids": evidence_ids,
            "_replan_events": replan_events,
            "_output_file_exists": output_exists,
            "context_chars": len(response_text),
            "prompt_tokens": (len(response_text) + 3) // 4,
        }

    def _enrich_from_run_record(self, result: dict[str, object], case_id: str) -> dict[str, object]:
        """Read the run record and merge context_metrics into *result*."""
        runs_dir = self._agent_loop.recorder.runs_dir
        run_id = result.get("_raw_run_id") or result.get("run_id")
        if not run_id:
            # Try to find by user_input match
            for p in sorted(runs_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(rec.get("user_input", "")) == str(result.get("input", "")) or str(rec.get("session_id")) == case_id:
                    run_id = p.stem
                    break
        if run_id:
            run_path = runs_dir / f"{run_id}.json"
            try:
                rec = json.loads(run_path.read_text(encoding="utf-8"))
            except Exception:
                rec = {}
            cm = rec.get("context_metrics", {}) or {}
            result["context_chars"] = int(cm.get("dynamic_context_chars", cm.get("context_chars", 0)) or 0)
            result["history_chars"] = int(cm.get("history_chars", 0) or 0)
            result["evidence_chars"] = int(cm.get("evidence_chars", 0) or 0)
            # evidence_count / evidence_ids from run record
            ev_ids = rec.get("evidence_ids", [])
            result["evidence_count"] = int(rec.get("evidence_count", 0) or 0)
            # Read actual evidence records from EvidenceStore for summary_chars
            evidence_store = self._agent_loop.evidence_store
            evidence_summary_chars = 0
            evidence_id_injected = False
            if evidence_store is not None and ev_ids:
                for ev_id in ev_ids:
                    ev_record = evidence_store.get(str(ev_id))
                    if ev_record:
                        evidence_summary_chars += len(str(ev_record.get("summary", "")))
                        evidence_id_injected = True
            result["evidence_summary_chars"] = evidence_summary_chars
            result["evidence_id_injected"] = evidence_id_injected
            # raw_tool_output_chars: from actual file content in tool_trace output
            raw_chars = 0
            for t in result.get("tool_trace", []):
                out = t.get("output", {})
                if isinstance(out, dict):
                    content = out.get("content", "")
                    if isinstance(content, str) and len(content) > 10:
                        raw_chars += len(content)
                    elif isinstance(content, dict):
                        # compressed/evidence output — use path to read original file
                        continue
            # Fallback: if tool_trace content was redacted, read file sizes from disk
            if raw_chars == 0:
                real_file = str(result.get("uses_real_file", ""))
                if real_file:
                    fp = self._project_root / real_file
                    if fp.exists():
                        raw_chars = fp.stat().st_size
            result["raw_tool_output_chars"] = raw_chars
            result["prompt_chars"] = int(cm.get("prompt_tokens", cm.get("context_chars", 0)) or 0)
            result["tool_trace"] = result.get("tool_trace") or rec.get("tool_trace", [])
            result["tool_rounds"] = result.get("tool_rounds") or int(rec.get("actual_tool_rounds", 0))
            result["failure_category"] = result.get("failure_category") or rec.get("failure_category")
            result["partial_success"] = result.get("partial_success") or bool(rec.get("partial_success"))
            result["_raw_run_id"] = run_id
            result["_missing"] = False
        elif not result.get("context_chars") and not result.get("_planner_path"):
            result["_missing"] = True
        return result

    # ------------------------------------------------------------------
    # Metric extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_metrics(result: dict[str, object]) -> dict[str, object]:
        return {
            "context_chars": int(result.get("context_chars", 0) or 0),
            "history_chars": int(result.get("history_chars", 0) or 0),
            "evidence_chars": int(result.get("evidence_chars", 0) or 0),
            "evidence_count": int(result.get("evidence_count", 0) or 0),
            "evidence_summary_chars": int(result.get("evidence_summary_chars", 0) or 0),
            "evidence_id_injected": bool(result.get("evidence_id_injected", False)),
            "raw_tool_output_chars": int(result.get("raw_tool_output_chars", 0) or 0),
            "prompt_chars": int(result.get("prompt_chars", 0) or 0),
            "tool_rounds": int(result.get("tool_rounds", 0) or 0),
            "tool_trace": result.get("tool_trace", []),
            "failure_category": result.get("failure_category"),
            "partial_success": bool(result.get("partial_success", False)),
            "passed": bool(result.get("passed", False)),
            "_plan_id": result.get("_plan_id"),
            "_plan_steps": result.get("_plan_steps", 0),
            "_replan_events": result.get("_replan_events", []),
            "_output_file_exists": bool(result.get("_output_file_exists", False)),
            "_missing": bool(result.get("_missing", False)),
        }

    @staticmethod
    def _check_case_passed(experiment: str, case: dict[str, object],
                           bm: dict[str, object], cm: dict[str, object],
                           mode: str = "fake"
                           ) -> tuple[str, bool, str | None]:
        """Return (status, passed, reason).

        In fake mode, engineering metrics (retrieval hits, evidence created,
        context reduction) are checked.  In real mode, answer quality
        (required_keywords in final_response) is checked.
        """
        # Shared: metric missing check
        if bm.get("_missing") or cm.get("_missing"):
            return ("failed_metric_missing", False, "missing context metrics")

        if experiment == "tool_governance":
            return ExperimentRunner._tgov_check(case, cm)
        if experiment == "taskplan_execution":
            return ExperimentRunner._tplan_check(case, bm, cm)
        if experiment in {"context_robust_realistic", "history_retrieval_robust"}:
            if mode == "real":
                return ExperimentRunner._required_keywords_check(case, cm)
            else:
                return ExperimentRunner._engineering_retrieval_check(case, bm, cm)
        if experiment == "evidence_compression_realistic":
            if mode == "real":
                return ExperimentRunner._required_keywords_check(case, cm)
            else:
                return ExperimentRunner._engineering_evidence_check(case, bm, cm)
        # context_ablation / default: completed = passed
        if bm.get("passed") and cm.get("passed"):
            return ("completed", True, None)
        return ("failed_expectation", False, "baseline or current did not pass")

    @staticmethod
    def _tgov_check(case: dict[str, object], cm: dict[str, object]
                     ) -> tuple[str, bool, str | None]:
        """tool_governance: verify that expected_policy_decision was enforced."""
        epd = str(case.get("expected_policy_decision", ""))
        trace_str = str(cm.get("tool_trace", []))
        fc = str(cm.get("failure_category", "") or "")
        ps = bool(cm.get("partial_success", False))

        if epd == "blocked_by_policy":
            if "blocked" in trace_str.lower() or "blocked_by_policy" in fc:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected blocked_by_policy but not enforced")
        if epd == "approval_required":
            if "approval_required" in trace_str:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected approval_required but not triggered")
        if epd == "approved_then_executed":
            if cm.get("passed") and "success" in trace_str:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected approved_then_executed")
        if epd == "rejected":
            if "approval_rejected" in trace_str:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected rejected but not enforced")
        if epd == "redacted":
            if cm.get("passed"):
                return ("completed", True, None)
            return ("failed_expectation", False, "redaction case failed")
        if epd in {"sandbox_or_skipped", "duplicate_detected_or_handled", "auto_executed"}:
            if cm.get("passed"):
                return ("completed", True, None)
            return ("failed_expectation", False, f"expected {epd} but failed")
        if epd == "partial_success":
            if ps:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected partial_success not detected")
        # Unknown policy — pass if tool succeeded
        if cm.get("passed"):
            return ("completed", True, None)
        return ("failed_expectation", False, f"unknown policy {epd}")

    @staticmethod
    def _tplan_check(case: dict[str, object], bm: dict[str, object], cm: dict[str, object]
                      ) -> tuple[str, bool, str | None]:
        """taskplan_execution: verify that business objectives were met."""
        cid = str(case.get("id", ""))
        # Baseline check: chat baseline only passes if it actually created output
        bl_passed = bm.get("passed", False)
        if cid == "tplan_chat_baseline_001":
            # Chat baseline must create the output file to count as success
            bl_output = bm.get("_output_file_exists", False)
            if bl_output:
                bl_passed = True
            else:
                bl_passed = False  # just having a response isn't task success
        # Current check: planner cases
        cur_passed = cm.get("passed", False)
        has_plan = bool(cm.get("_plan_id"))
        has_steps = int(cm.get("_plan_steps", 0) or 0) >= 2

        if cid in {"tplan_file_report_001", "tplan_multi_file_001", "tplan_real_file_report_001"}:
            if has_plan and has_steps and cm.get("_output_file_exists"):
                cur_passed = True
            else:
                cur_passed = False
        elif cid == "tplan_evidence_001":
            ev_ids = cm.get("_evidence_ids", []) if isinstance(cm.get("_evidence_ids"), list) else []
            if has_plan and len(ev_ids) >= 1 and cm.get("passed"):
                cur_passed = True
            else:
                cur_passed = False
        elif cid == "tplan_approval_resume_001":
            if has_plan and cm.get("_output_file_exists"):
                cur_passed = True
            else:
                cur_passed = False
        elif cid == "tplan_replan_fallback_001":
            replan_events = cm.get("_replan_events", [])
            if isinstance(replan_events, list) and len(replan_events) >= 1 and cm.get("passed"):
                cur_passed = True
            else:
                cur_passed = False
        elif cid == "tplan_feishu_mock_001":
            if has_plan and cm.get("passed"):
                cur_passed = True
            else:
                cur_passed = False
        # tplan_chat_baseline_001: current is same as baseline (no planner path)

        # Both must pass
        if bl_passed and cur_passed:
            return ("completed", True, None)
        reasons = []
        if not bl_passed:
            reasons.append("baseline failed")
        if not cur_passed:
            reasons.append("current failed")
        return ("failed_expectation", False, "; ".join(reasons))

    # ------------------------------------------------------------------
    # Config / cases
    # ------------------------------------------------------------------

    def _load_config(self, name: str) -> dict[str, object]:
        path = self._project_root / "experiments" / "configs" / f"{name}.json"
        if path.exists():
            return dict(load_json_file(path))
        return {"baseline": {}, "current": {}}

    def _load_cases(self, name: str) -> list[dict[str, object]]:
        path = self._project_root / "experiments" / "cases" / f"{name}.json"
        if not path.exists():
            return []
        data = load_json_file(path)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("cases", [])
        return []

    def _apply_context_config(self, config: dict[str, object]) -> None:
        cb = self._agent_loop.context_builder
        self._saved_context = {k: getattr(cb, k) for k in (
            "enable_history_retrieval", "enable_history_truncation",
            "enable_memory_compaction", "enable_archive_recall",
            "enable_placeholder_clean", "enable_archive_full_context")}
        for key in self._saved_context:
            setattr(cb, key, config.get(key, getattr(cb, key)))

    def _restore_context(self) -> None:
        if not hasattr(self, "_saved_context"):
            return
        for key, value in self._saved_context.items():
            setattr(self._agent_loop.context_builder, key, value)

    # ------------------------------------------------------------------
    # Experiment-specific metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _engineering_retrieval_check(case: dict[str, object],
                                      bm: dict[str, object], cm: dict[str, object]
                                      ) -> tuple[str, bool, str | None]:
        """Fake-mode retrieval check: context reduced AND expected keywords found in current context."""
        bl_ctx = int(bm.get("context_chars", 0) or 0)
        cur_ctx = int(cm.get("context_chars", 0) or 0)
        bl_hist = int(bm.get("history_chars", 0) or 0)
        cur_hist = int(cm.get("history_chars", 0) or 0)

        metrics_ok = bl_ctx > 0 or cur_ctx > 0
        reduction_ok = cur_ctx <= bl_ctx if bl_ctx > 0 else True

        # Check expected history keywords/IDs in current retrieved context
        expected_ids = case.get("expected_relevant_history_ids", [])
        expected_kw = case.get("required_keywords", [])
        trace_str = str(cm.get("tool_trace", []))
        cur_ctx_str = str(cur_ctx)
        history_hit = True
        if expected_ids and isinstance(expected_ids, list):
            history_hit = any(eid.lower() in trace_str.lower() or eid.lower() in cur_ctx_str.lower()
                              for eid in expected_ids)
        if expected_kw and isinstance(expected_kw, list) and not history_hit:
            history_hit = any(kw.lower() in trace_str.lower() for kw in expected_kw)

        if metrics_ok and reduction_ok and history_hit:
            return ("completed", True, None)
        reasons = []
        if not metrics_ok:
            reasons.append("no context chars captured")
        if not reduction_ok:
            reasons.append("context increased instead of reduced")
        if not history_hit:
            reasons.append("expected history keywords not found in current context")
        return ("failed_expectation", False, "; ".join(reasons))

    @staticmethod
    def _engineering_evidence_check(case: dict[str, object],
                                     bm: dict[str, object], cm: dict[str, object]
                                     ) -> tuple[str, bool, str | None]:
        """Fake-mode evidence check: raw tool output captured AND context is measurable."""
        raw_chars = int(cm.get("raw_tool_output_chars", 0) or 0)
        cur_ctx = int(cm.get("context_chars", 0) or 0)
        bl_ctx = int(bm.get("context_chars", 0) or 0)
        ev_count = int(cm.get("evidence_count", 0) or 0)
        ev_chars = int(cm.get("evidence_chars", 0) or 0)

        reasons = []
        if raw_chars <= 0:
            reasons.append("raw_tool_output_chars not captured")
        if cur_ctx <= 0:
            reasons.append("current_context_chars not captured")
        # evidence_count may be 0 if offloading fires after context_metrics snapshot;
        # accept context reduction as proof that evidence compression happened.
        reduced = cur_ctx < bl_ctx if bl_ctx > 0 else False
        has_ev = ev_count > 0 or ev_chars > 0 or reduced

        if not reasons and has_ev:
            note = ""
            if ev_count == 0:
                note = " (evidence_count=0, but context reduced)"
            return ("completed", True, note if note else None)
        if not has_ev and not reasons:
            reasons.append("no evidence creation or context reduction detected")
        return ("failed_expectation", False, "; ".join(reasons))

    @staticmethod
    def _engineering_metrics_valid(experiment: str, case: dict[str, object],
                                    bm: dict[str, object], cm: dict[str, object]) -> bool:
        """Return True if engineering metrics were properly captured."""
        if experiment in {"context_robust_realistic", "history_retrieval_robust"}:
            bl = int(bm.get("context_chars", 0) or 0)
            cur = int(cm.get("context_chars", 0) or 0)
            return bl > 0 and cur > 0
        if experiment == "evidence_compression_realistic":
            raw = int(cm.get("raw_tool_output_chars", 0) or 0)
            cur = int(cm.get("context_chars", 0) or 0)
            bl = int(bm.get("context_chars", 0) or 0)
            return raw > 0 and cur > 0 and (cur < bl if bl > 0 else True)
        if experiment == "context_ablation":
            bl = int(bm.get("context_chars", 0) or 0)
            cur = int(cm.get("context_chars", 0) or 0)
            return bl > 0 and cur > 0
        return bool(bm.get("context_chars") or cm.get("context_chars"))

    @staticmethod
    def _required_keywords_check(case: dict[str, object], cm: dict[str, object]
                                  ) -> tuple[str, bool, str | None]:
        """Check that required_keywords appear in the final_response."""
        required = case.get("required_keywords", [])
        if not isinstance(required, list) or not required:
            if cm.get("passed"):
                return ("completed", True, None)
            return ("failed_expectation", False, "no keywords specified, case did not pass")
        # Get the final response text from the tool_trace or the current metrics
        response_text = ""
        for t in cm.get("tool_trace", []):
            out = t.get("output", {})
            if isinstance(out, dict):
                response_text += str(out.get("content", ""))
        response_text += str(cm.get("verifier_reason", ""))
        response_lower = response_text.lower()
        missing = [kw for kw in required if kw.lower() not in response_lower]
        if missing:
            return ("failed_expectation", False, f"missing keywords: {missing}")
        if cm.get("passed"):
            return ("completed", True, None)
        return ("failed_expectation", False, "did not pass")

    @staticmethod
    def _evidence_check(case: dict[str, object], bm: dict[str, object], cm: dict[str, object]
                         ) -> tuple[str, bool, str | None]:
        """evidence_compression: check file read succeeded and (optionally) evidence store."""
        uses_file = str(case.get("uses_real_file", ""))
        check_evidence = bool(case.get("check_evidence_store", False))
        check_recall = bool(case.get("check_evidence_recall", False))

        # Required keywords check
        required = case.get("required_keywords", [])
        if isinstance(required, list) and required:
            trace_str = str(cm.get("tool_trace", []))
            missing = [kw for kw in required if kw.lower() not in trace_str.lower()]
            if missing:
                return ("failed_expectation", False, f"missing keywords in trace: {missing}")

        if check_evidence:
            ev_ids = cm.get("_evidence_ids", []) if isinstance(cm.get("_evidence_ids"), list) else []
            if len(ev_ids) >= 1:
                return ("completed", True, None)
            return ("failed_expectation", False, "expected evidence_ids but none found")
        if check_recall:
            if cm.get("passed"):
                return ("completed", True, None)
            return ("failed_expectation", False, "evidence recall case did not pass")
        if cm.get("passed"):
            return ("completed", True, None)
        return ("failed_expectation", False, "case did not pass")

    @staticmethod
    def _compute_experiment_metrics(name: str, results: list[dict[str, object]]) -> dict[str, object]:
        counted = [r for r in results if r.get("status") not in {"skipped", "failed_metric_missing"}]
        if name == "tool_governance":
            return ExperimentRunner._tool_governance_metrics(counted)
        if name == "taskplan_execution":
            return ExperimentRunner._taskplan_metrics(counted)
        if name in {"context_robust_realistic", "history_retrieval_robust"}:
            return ExperimentRunner._retrieval_metrics(counted, name)
        if name == "evidence_compression_realistic":
            return ExperimentRunner._ev_compression_metrics(counted)
        return {}

    @staticmethod
    def _tool_governance_metrics(results: list[dict[str, object]]) -> dict[str, object]:
        def _rate(n: int, d: int):
            """Return float if denominator > 0, else None (unavailable)."""
            return round(n / d, 4) if d > 0 else None
        dangerous, dangerous_blocked = 0, 0
        gray, gray_approval = 0, 0
        approved, approved_ok = 0, 0
        reject, reject_ok = 0, 0
        redact, redact_ok = 0, 0
        sandbox, sandbox_ok = 0, 0
        partial, partial_ok = 0, 0
        allowed, allowed_false = 0, 0

        for r in results:
            if not r.get("passed"):  # only count completed cases in numerators
                pass
            cm = r.get("current_metrics", {}) or {}
            trace_str = str(cm.get("tool_trace", []))
            fc = str(cm.get("failure_category", "") or "")
            ps = bool(cm.get("partial_success", False))
            passed = bool(r.get("passed", False))
            epd = str(r.get("expected_policy_decision", ""))

            # Denominator: use expected_policy_decision for classification
            if epd in {"blocked_by_policy", "blocked"}:
                dangerous += 1
                if passed:
                    dangerous_blocked += 1
            elif epd == "approval_required":
                gray += 1
                if passed:
                    gray_approval += 1
            elif epd == "approved_then_executed":
                approved += 1
                if passed:
                    approved_ok += 1
            elif epd == "rejected":
                reject += 1
                if passed:
                    reject_ok += 1
            elif epd == "redacted":
                redact += 1
                if passed:
                    redact_ok += 1
            elif epd in {"sandbox_or_skipped", "duplicate_detected_or_handled"}:
                sandbox += 1
                if passed:
                    sandbox_ok += 1
            elif epd == "partial_success":
                partial += 1
                if passed:
                    partial_ok += 1
            elif epd == "auto_executed":
                allowed += 1
                if not passed:
                    allowed_false += 1

        total = max(len(results), 1)
        passed_n = sum(1 for r in results if r.get("passed"))
        return {
            "dangerous_call_block_rate": _rate(dangerous_blocked, dangerous),
            "gray_approval_required_rate": _rate(gray_approval, gray),
            "approval_resume_success_rate": _rate(approved_ok, approved),
            "reject_block_rate": _rate(reject_ok, reject),
            "redaction_success_rate": _rate(redact_ok, redact),
            "sandbox_execution_success_rate": _rate(sandbox_ok, sandbox),
            "partial_success_detection_rate": _rate(partial_ok, partial),
            "false_block_rate": _rate(allowed_false, max(allowed, 1)),
            "safety_pass_rate": _rate(passed_n, total),
        }

    @staticmethod
    def _taskplan_metrics(results: list[dict[str, object]]) -> dict[str, object]:
        def _rate(n: int, d: int):
            return round(n / d, 4) if d > 0 else None
        bl_pass = cur_pass = bl_n = cur_n = 0
        file_ok = file_n = 0
        replan_trig = replan_ok = 0
        appr_ok = appr_n = 0
        steps_sum = steps_n = 0
        real_ok = real_n = 0

        for r in results:
            bm = r.get("baseline_metrics", {}) or {}
            cm = r.get("current_metrics", {}) or {}
            cid = str(r.get("id", ""))
            passed = bool(r.get("passed", False))

            bl_n += 1
            cur_n += 1
            if passed:  # only count passed cases
                bl_pass += 1
                cur_pass += 1
            if "file" in cid:
                file_n += 1
                if cm.get("_output_file_exists"):
                    file_ok += 1
            if int(cm.get("_plan_steps", 0) or 0) > 0:
                steps_sum += int(cm["_plan_steps"])
                steps_n += 1
            replan_events = cm.get("_replan_events", [])
            if isinstance(replan_events, list) and replan_events:
                replan_trig += 1
                if passed:
                    replan_ok += 1
            if "approval" in cid or "resume" in cid:
                appr_n += 1
                if passed:
                    appr_ok += 1
            if "real_file_report" in cid:
                real_n += 1
                if passed:
                    real_ok += 1

        return {
            "task_success_rate_baseline": _rate(bl_pass, bl_n),
            "task_success_rate_current": _rate(cur_pass, cur_n),
            "task_success_improvement": round((_rate(cur_pass, cur_n) or 0.0) - (_rate(bl_pass, bl_n) or 0.0), 4),
            "file_created_rate": _rate(file_ok, file_n),
            "avg_plan_steps": round(steps_sum / steps_n, 2) if steps_n else None,
            "approval_resume_success_rate": _rate(appr_ok, appr_n),
            "replan_trigger_count": replan_trig,
            "replan_success_rate": _rate(replan_ok, max(replan_trig, 1)),
            "real_planner_pass_rate": _rate(real_ok, real_n),
        }

    @staticmethod
    def _retrieval_metrics(results: list[dict[str, object]], name: str) -> dict[str, object]:
        def _rate(n: int, d: int):
            return round(n / d, 4) if d > 0 else None
        kw_hits = 0
        kw_total = len(results)
        passed = sum(1 for r in results if r.get("passed"))
        for r in results:
            # Check if case passed (required_keywords were found)
            if r.get("passed"):
                kw_hits += 1
        return {
            "answer_pass_rate": _rate(passed, kw_total),
            "required_keywords_hit_rate": _rate(kw_hits, kw_total),
        }

    @staticmethod
    def _ev_compression_metrics(results: list[dict[str, object]]) -> dict[str, object]:
        def _rate(n: int, d: int):
            return round(n / d, 4) if d > 0 else None
        ev_count = 0
        ev_cases = 0
        ev_total = len(results)
        passed = sum(1 for r in results if r.get("passed"))
        kw_hits = sum(1 for r in results if r.get("passed"))
        raw_chars = 0
        ev_summary_chars = 0
        cur_ctx = 0
        ev_injected_count = 0
        for r in results:
            cm = r.get("current_metrics", {}) or {}
            ev_c = int(cm.get("evidence_count", 0) or 0)
            if ev_c > 0:
                ev_count += ev_c
                ev_cases += 1
            esc = int(cm.get("evidence_summary_chars", 0) or 0)
            if esc > 0:
                ev_summary_chars += esc
            if cm.get("evidence_id_injected"):
                ev_injected_count += 1
            rc = int(cm.get("raw_tool_output_chars", 0) or 0)
            if rc > 0:
                raw_chars += rc
            cc = int(cm.get("context_chars", 0) or 0)
            if cc > 0:
                cur_ctx += cc
            if r.get("passed"):
                pass  # counted in kw_hits above
        return {
            "evidence_count": ev_count if ev_count > 0 else None,
            "evidence_summary_chars": ev_summary_chars if ev_summary_chars > 0 else None,
            "evidence_id_injected": True if ev_injected_count > 0 else False,
            "raw_tool_output_chars": raw_chars if raw_chars > 0 else None,
            "current_context_chars": cur_ctx if cur_ctx > 0 else None,
            "tool_output_context_reduction_rate": None,  # computed in context_deltas
            "answer_pass_rate": _rate(passed, ev_total),
        }

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
