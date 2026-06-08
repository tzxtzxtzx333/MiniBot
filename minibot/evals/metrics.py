"""Benchmark metric helpers."""

from __future__ import annotations

from collections import Counter


def compute_pass_rate(passed: int, total: int) -> float:
    """Return a pass rate from actual counted results."""

    return 0.0 if total == 0 else passed / total


def average(values: list[float]) -> float:
    """Return the arithmetic mean or zero for an empty list."""

    return 0.0 if not values else round(sum(values) / len(values), 2)


def summarize_case_metrics(case_results: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate benchmark report metrics from per-case execution results."""

    counted = [item for item in case_results if bool(item.get("counted_in_pass_rate"))]
    passed = [item for item in counted if bool(item.get("passed"))]
    latencies = [float(item.get("latency_ms", 0.0)) for item in counted]
    tool_rounds = [float(item.get("tool_rounds", 0.0)) for item in counted]
    failure_counter = Counter(
        str(item["failure_category"])
        for item in counted
        if item.get("failure_category") not in {None, ""}
    )
    downgrade_counter = Counter(
        str(item["downgrade_reason"])
        for item in counted
        if item.get("downgrade_reason") not in {None, ""}
    )
    verifier_counter = Counter(str(item["verifier_reason"]) for item in counted if item.get("verifier_reason"))
    verifier_mode_counter = Counter(str(item["verifier_mode"]) for item in counted if item.get("verifier_mode"))
    verifier_failure_counter = Counter(
        str(item["verifier_failure_category"])
        for item in counted
        if item.get("verifier_failure_category") not in {None, ""}
    )
    retry_counts = [float(item.get("retry_count", 0.0)) for item in counted]
    partial_success_count = sum(1 for item in counted if bool(item.get("partial_success")))
    return {
        "counted_cases": len(counted),
        "passed_cases": len(passed),
        "pass_rate": compute_pass_rate(len(passed), len(counted)),
        "avg_latency": average(latencies),
        "avg_tool_rounds": average(tool_rounds),
        "tool_rounds": average(tool_rounds),
        "retry_count": average(retry_counts),
        "partial_success": partial_success_count,
        "failure_category": dict(failure_counter),
        "downgrade_reason": dict(downgrade_counter),
        "verifier_reason": dict(verifier_counter),
        "verifier_mode": dict(verifier_mode_counter),
        "verifier_failure_category": dict(verifier_failure_counter),
    }
