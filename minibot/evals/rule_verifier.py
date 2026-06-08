"""Rule-based benchmark verification."""

from __future__ import annotations


class RuleVerifier:
    """Check structured expectations against a persisted run record."""

    def verify(self, run_record: dict[str, object], expected_behavior: list[str]) -> tuple[bool, str]:
        """Return pass/fail plus a structured reason."""

        matched = 0
        failures: list[str] = []
        for expectation in expected_behavior:
            if self._matches(run_record, expectation):
                matched += 1
                continue
            failures.append(expectation)
        if not expected_behavior:
            return True, "no expected behavior rules configured"
        if not failures:
            return True, f"matched {matched}/{len(expected_behavior)} rules"
        return False, f"missing rules: {', '.join(failures)}"

    def _matches(self, run_record: dict[str, object], expectation: str) -> bool:
        if ":" not in expectation:
            if expectation == "context_metrics_present":
                return self._has_context_metrics(run_record)
            if expectation == "final_response_not_empty":
                return self._has_non_empty_response(run_record)
            return expectation.lower() in str(run_record).lower()
        rule, payload = expectation.split(":", 1)
        if rule == "final_response_contains":
            return payload in self._response_text(run_record)
        if rule == "final_response_not_empty":
            return self._has_non_empty_response(run_record)
        if rule == "context_metrics_present":
            return self._has_context_metrics(run_record)
        if rule == "avg_prompt_tokens_present":
            if "avg_prompt_tokens" in run_record:
                try:
                    return float(run_record.get("avg_prompt_tokens", 0)) >= float(payload)
                except (TypeError, ValueError):
                    return False
            metrics = run_record.get("context_metrics", {})
            try:
                return isinstance(metrics, dict) and float(metrics.get("prompt_tokens", -1)) >= float(payload)
            except (TypeError, ValueError):
                return False
        if rule in {"key_fact_preserved", "key_facts_preserved"}:
            metrics = run_record.get("context_metrics", {})
            return bool(dict(metrics).get("key_facts_preserved")) is (payload.lower() == "true")
        if rule == "tool_call_contains":
            return any(str(item.get("tool_name")) == payload for item in run_record.get("tool_calls", []))
        if rule == "tool_trace_contains":
            return any(str(item.get("tool_name")) == payload for item in run_record.get("tool_trace", []))
        if rule == "failure_category":
            return str(run_record.get("failure_category")) == payload
        if rule == "retry_count_at_least":
            return int(run_record.get("retry_count", 0)) >= int(payload)
        if rule == "partial_success":
            return bool(run_record.get("partial_success")) is (payload.lower() == "true")
        if rule == "downgrade_reason":
            return str(run_record.get("downgrade_reason")) == payload
        if rule == "verifier_reason_contains":
            return payload in str(run_record.get("verifier_reason", ""))
        if rule == "multi_round":
            return bool(run_record.get("multi_round")) is (payload.lower() == "true")
        if rule == "actual_tool_rounds":
            if payload.startswith(">="):
                return int(run_record.get("actual_tool_rounds", 0)) >= int(payload[2:])
            return int(run_record.get("actual_tool_rounds", 0)) >= int(payload)
        if rule == "tool_round_count_at_least":
            return int(run_record.get("actual_tool_rounds", 0)) >= int(payload)
        if rule == "stop_reason":
            return str(run_record.get("stop_reason")) == payload
        if rule == "actual_tool_calls_total":
            if payload.startswith("<="):
                return int(run_record.get("actual_tool_calls_total", 0)) <= int(payload[3:])
            if payload.startswith(">="):
                return int(run_record.get("actual_tool_calls_total", 0)) >= int(payload[2:])
            return int(run_record.get("actual_tool_calls_total", 0)) >= int(payload)
        if rule == "final_answer_used_tool_results":
            return bool(run_record.get("final_answer_used_tool_results")) is (payload.lower() == "true")
        if rule == "subagent_trace_contains":
            return any(str(item.get("agent")) == payload for item in run_record.get("subagent_trace", []))
        if rule == "tool_result_status":
            tool_name, status = payload.split(":", 1)
            return any(
                str(item.get("tool_name")) == tool_name and str(item.get("status")) == status
                for item in run_record.get("tool_results", [])
            )
        if rule == "tool_result_failure":
            tool_name, category = payload.split(":", 1)
            return any(
                str(item.get("tool_name")) == tool_name and str(item.get("failure_category")) == category
                for item in run_record.get("tool_results", [])
            )
        if rule == "tool_result_metadata":
            tool_name, key, expected = payload.split(":", 2)
            for item in run_record.get("tool_results", []):
                if str(item.get("tool_name")) != tool_name:
                    continue
                metadata = item.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                actual = metadata.get(key)
                if isinstance(actual, bool):
                    return actual is (expected.lower() == "true")
                if str(actual) == expected:
                    return True
            return False
        return payload.lower() in str(run_record).lower()

    @staticmethod
    def _has_context_metrics(run_record: dict[str, object]) -> bool:
        metrics = run_record.get("context_metrics", {})
        return (
            isinstance(metrics, dict)
            and "prompt_tokens" in metrics
            and "context_chars" in metrics
        )

    @staticmethod
    def _response_text(run_record: dict[str, object]) -> str:
        for key in ("final_response", "response", "final_output", "output"):
            value = run_record.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @classmethod
    def _has_non_empty_response(cls, run_record: dict[str, object]) -> bool:
        return bool(cls._response_text(run_record).strip())
