"""Experiment report — generate Markdown summary from raw report JSONs.

All numbers are extracted from the report file.  Nothing is hardcoded.
Tables vary by experiment type.
"""

from __future__ import annotations

import json
from pathlib import Path

_CONTEXT_METRICS = {
    "avg_context_chars_baseline",
    "avg_context_chars_current",
    "avg_history_chars_baseline",
    "avg_history_chars_current",
    "avg_prompt_chars_baseline",
    "avg_prompt_chars_current",
    "avg_evidence_chars_current",
    "context_reduction_rate",
}
_ANSWER_METRICS = {"answer_pass_rate", "required_keywords_hit_rate"}
_GOVERNANCE_METRICS = {
    "dangerous_call_block_rate",
    "gray_approval_required_rate",
    "approval_resume_success_rate",
    "reject_block_rate",
    "redaction_success_rate",
    "sandbox_execution_success_rate",
    "partial_success_detection_rate",
    "false_block_rate",
    "safety_pass_rate",
}
_TASKPLAN_METRICS = {
    "task_success_rate_baseline",
    "task_success_rate_current",
    "task_success_improvement",
    "file_created_rate",
    "avg_plan_steps",
    "approval_resume_success_rate",
    "replan_trigger_count",
    "replan_success_rate",
    "real_planner_pass_rate",
}
_EVIDENCE_METRICS = {
    "evidence_count",
    "answer_pass_rate",
    "required_keywords_hit_rate",
    "evidence_search_hit_rate",
}

# Label overrides for experiment types
_EXPERIMENT_LABELS = {
    "context_ablation": "构造长历史消融实验（非真实泛化）",
    "context_robust_realistic": "真实长对话稳健性实验（可用于简历）",
    "history_retrieval_robust": "HISTORY 检索实验",
    "evidence_compression_realistic": "真实文档 evidence 压缩实验",
}


def summarize_reports(report_paths: list[Path], output_path: Path | None = None) -> str:
    lines: list[str] = []
    lines.append("# Experiment Summary\n")
    lines.append(f"_generated from {len(report_paths)} report(s)_\n")

    for rp in report_paths:
        if not rp.exists():
            lines.append(f"⚠️ **Report not found**: `{rp}`\n")
            continue
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            lines.append(f"⚠️ **Invalid report**: `{rp}`\n")
            continue

        name = data.get("experiment", rp.stem)
        mode = data.get("mode", "?")
        label = _EXPERIMENT_LABELS.get(name, "")
        summ = data.get("summary", {}) or {}
        header = f"## {name}  ({mode} mode)"
        if label:
            header += f" — {label}"
        lines.append(header + "\n")

        # Status counts
        lines.append(f"- total: {data.get('total_cases','?')}")
        lines.append(f"- completed: {data.get('completed_cases','?')}")
        lines.append(f"- **passed**: {data.get('passed_cases','?')}")
        lines.append(f"- failed_metric_missing: {data.get('failed_metric_missing','?')}")
        lines.append(f"- failed_expectation: {data.get('failed_expectation','?')}")
        lines.append(f"- skipped: {data.get('skipped_cases','?')}")
        lines.append(f"- pass_rate: {data.get('pass_rate','?')}\n")

        # Typed metric table
        typed_keys = _typed_metric_keys(name, summ)
        if typed_keys:
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            for key in typed_keys:
                val = summ.get(key, "—")
                if isinstance(val, float):
                    val = f"{val:.4f}"
                lines.append(f"| {key} | {val} |")
            lines.append("")

        # Per-case detail
        results = data.get("results", [])
        if isinstance(results, list) and results:
            lines.append("<details><summary>Per-case metrics</summary>\n")
            lines.append("| case | status | passed | bl ctx | cur ctx |")
            lines.append("|---|---|---|---|---|")
            for r in results:
                rid = r.get("id", "?")
                st = r.get("status", "?")
                pa = r.get("passed", "?")
                bm = (
                    (r.get("baseline_metrics") or {})
                    if isinstance(r.get("baseline_metrics"), dict)
                    else {}
                )
                cm = (
                    (r.get("current_metrics") or {})
                    if isinstance(r.get("current_metrics"), dict)
                    else {}
                )
                lines.append(
                    f"| {rid} | {st} | {pa} | {bm.get('context_chars','—')} | {cm.get('context_chars','—')} |"
                )
            lines.append("\n</details>\n")

    # Credibility notice
    lines.append("## 数据可信性说明\n")
    lines.append("- `failed_metric_missing` 不计入优化结论；")
    lines.append("- fake mode 不证明真实模型能力；")
    lines.append("- 当前报告不得直接用于简历，除非所有关键指标有效。\n")

    md = "\n".join(lines)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")
    return md


def _typed_metric_keys(name: str, summary: dict[str, object]) -> list[str]:
    """Return metric keys relevant to this experiment type."""
    available = {k for k in summary if summary.get(k) not in (None, "")}
    candidates: set[str] = set()
    if "context_ablation" in name or "context_robust" in name:
        candidates = _CONTEXT_METRICS | _ANSWER_METRICS
    elif "history_retrieval" in name:
        candidates = _CONTEXT_METRICS | _ANSWER_METRICS
    elif "tool_governance" in name:
        candidates = _GOVERNANCE_METRICS
    elif "taskplan" in name:
        candidates = _TASKPLAN_METRICS
    elif "evidence_compression" in name:
        candidates = _EVIDENCE_METRICS | _CONTEXT_METRICS
    return sorted(candidates & available)
