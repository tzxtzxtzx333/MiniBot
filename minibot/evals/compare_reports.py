"""Benchmark report comparison helpers."""

from __future__ import annotations

from pathlib import Path

from minibot.json_utils import load_json_file


class ReportComparator:
    """Compare two benchmark reports and summarize regressions and improvements."""

    def compare(self, left: Path, right: Path) -> dict[str, object]:
        left_report = dict(load_json_file(left))
        right_report = dict(load_json_file(right))
        left_results = {str(item["id"]): item for item in left_report.get("results", [])}
        right_results = {str(item["id"]): item for item in right_report.get("results", [])}

        new_failures = sorted(
            case_id
            for case_id, result in right_results.items()
            if not bool(result.get("passed")) and bool(left_results.get(case_id, {}).get("passed", True))
        )
        fixed_failures = sorted(
            case_id
            for case_id, result in right_results.items()
            if bool(result.get("passed")) and not bool(left_results.get(case_id, {}).get("passed", True))
        )
        metric_changes = {
            metric: {
                "left": left_report.get(metric),
                "right": right_report.get(metric),
                "delta": round(float(right_report.get(metric, 0.0)) - float(left_report.get(metric, 0.0)), 4),
            }
            for metric in ("pass_rate", "avg_latency", "avg_tool_rounds", "tool_rounds", "retry_count", "partial_success")
        }
        avg_prompt_tokens_before = left_report.get("avg_prompt_tokens", 0.0)
        avg_prompt_tokens_after = right_report.get("avg_prompt_tokens", 0.0)
        avg_context_chars_before = left_report.get("avg_context_chars", 0.0)
        avg_context_chars_after = right_report.get("avg_context_chars", 0.0)
        avg_dynamic_context_tokens_before = left_report.get("avg_dynamic_context_tokens", 0.0)
        avg_dynamic_context_tokens_after = right_report.get("avg_dynamic_context_tokens", 0.0)
        token_reduction_rate = None
        dynamic_token_reduction_rate = None
        try:
            before_value = float(avg_prompt_tokens_before)
            after_value = float(avg_prompt_tokens_after)
            if before_value != 0:
                token_reduction_rate = round((before_value - after_value) / before_value, 4)
            else:
                token_reduction_rate = 0.0
        except (TypeError, ValueError):
            token_reduction_rate = None
        try:
            before_value = float(avg_dynamic_context_tokens_before)
            after_value = float(avg_dynamic_context_tokens_after)
            if before_value != 0:
                dynamic_token_reduction_rate = round((before_value - after_value) / before_value, 4)
            else:
                dynamic_token_reduction_rate = 0.0
        except (TypeError, ValueError):
            dynamic_token_reduction_rate = None
        return {
            "left_cases": left_report.get("total_cases", 0),
            "right_cases": right_report.get("total_cases", 0),
            "new_failures": new_failures,
            "fixed_failures": fixed_failures,
            "metric_changes": metric_changes,
            "capability_status_changes": self._diff_mapping(left_report.get("capability_status", {}), right_report.get("capability_status", {})),
            "mock_tools_used_changes": self._diff_list(left_report.get("mock_tools_used", []), right_report.get("mock_tools_used", [])),
            "real_tools_used_changes": self._diff_list(left_report.get("real_tools_used", []), right_report.get("real_tools_used", [])),
            "avg_prompt_tokens_before": avg_prompt_tokens_before,
            "avg_prompt_tokens_after": avg_prompt_tokens_after,
            "avg_context_chars_before": avg_context_chars_before,
            "avg_context_chars_after": avg_context_chars_after,
            "avg_dynamic_context_tokens_before": avg_dynamic_context_tokens_before,
            "avg_dynamic_context_tokens_after": avg_dynamic_context_tokens_after,
            "token_reduction_rate": token_reduction_rate,
            "dynamic_token_reduction_rate": dynamic_token_reduction_rate,
        }

    @staticmethod
    def _diff_mapping(left: object, right: object) -> dict[str, dict[str, object]]:
        left_map = dict(left or {})
        right_map = dict(right or {})
        keys = sorted(set(left_map) | set(right_map))
        return {
            key: {"left": left_map.get(key), "right": right_map.get(key)}
            for key in keys
            if left_map.get(key) != right_map.get(key)
        }

    @staticmethod
    def _diff_list(left: object, right: object) -> dict[str, list[object]]:
        left_set = {item for item in list(left or [])}
        right_set = {item for item in list(right or [])}
        return {
            "added": sorted(right_set - left_set),
            "removed": sorted(left_set - right_set),
        }
