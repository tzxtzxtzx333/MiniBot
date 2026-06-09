"""Experiment metrics — formulas backed by raw report data only."""

from __future__ import annotations


def compute_context_reduction_rate(baseline_avg: float, current_avg: float) -> float:
    """``(baseline - current) / baseline`` — returns 0.0 when baseline is 0."""
    if baseline_avg <= 0:
        return 0.0
    return round((baseline_avg - current_avg) / baseline_avg, 4)


def extract_paired_metrics(results: list[dict[str, object]]) -> dict[str, object]:
    """Extract baseline / current paired metrics from experiment results.

    Skipped cases are excluded from aggregation.
    """
    completed = [r for r in results if r.get("status") != "skipped"]
    count = max(len(completed), 1)

    total_bl_context = 0
    total_bl_prompt = 0
    total_cur_context = 0
    total_cur_prompt = 0
    memory_passed = 0
    memory_total = 0
    long_passed = 0
    long_total = 0
    hist_bl_chars = 0
    hist_cur_chars = 0
    ev_cur_chars = 0
    count_ev = 0

    for r in completed:
        bm = r.get("baseline_metrics", {})
        cm = r.get("current_metrics", {})
        if not isinstance(bm, dict):
            bm = {}
        if not isinstance(cm, dict):
            cm = {}

        total_bl_context += int(bm.get("context_chars", 0) or 0)
        total_bl_prompt += int(bm.get("prompt_chars", 0) or 0)
        total_cur_context += int(cm.get("context_chars", 0) or 0)
        total_cur_prompt += int(cm.get("prompt_chars", 0) or 0)
        hist_bl_chars += int(bm.get("history_chars", 0) or 0)
        hist_cur_chars += int(cm.get("history_chars", 0) or 0)
        ev_c = int(cm.get("evidence_chars", 0) or 0)
        if ev_c > 0:
            ev_cur_chars += ev_c
            count_ev += 1

        cat = str(r.get("category", ""))
        if cat in {"memory", "memory_recall"}:
            memory_total += 1
            if bm.get("passed"):
                memory_passed += 1
        if cat in {"context", "long_context"}:
            long_total += 1
            if bm.get("passed"):
                long_passed += 1

    bl_avg = round(total_bl_context / count, 2)
    cur_avg = round(total_cur_context / count, 2)

    return {
        "avg_context_chars_baseline": bl_avg,
        "avg_context_chars_current": cur_avg,
        "avg_prompt_chars_baseline": round(total_bl_prompt / count, 2),
        "avg_prompt_chars_current": round(total_cur_prompt / count, 2),
        "avg_history_chars_baseline": round(hist_bl_chars / count, 2),
        "avg_history_chars_current": round(hist_cur_chars / count, 2),
        "avg_evidence_chars_current": round(ev_cur_chars / count_ev, 2) if count_ev else 0.0,
        "context_reduction_rate": compute_context_reduction_rate(bl_avg, cur_avg),
        "memory_recall_pass_rate": round(memory_passed / memory_total, 4) if memory_total else 0.0,
        "long_context_pass_rate": round(long_passed / long_total, 4) if long_total else 0.0,
        "completed_cases": len(completed),
    }
