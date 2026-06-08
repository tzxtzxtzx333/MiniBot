"""Retry and downgrade helpers for tool execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from minibot.tools.base import ToolResult


@dataclass(slots=True)
class RetryOutcome:
    """Structured retry result."""

    result: ToolResult
    retry_count: int
    retry_errors: list[str]
    downgrade_reason: str | None = None


class RetryManager:
    """Retry retryable tool failures, then optionally downgrade."""

    def __init__(self, config: dict[str, object] | None = None) -> None:
        retry_config = dict((config or {}).get("retry", {}))
        self.strategy = str(retry_config.get("strategy", "fixed"))
        self.base_delay_seconds = float(retry_config.get("base_delay_seconds", 0.0))
        configured = retry_config.get("retryable_failure_categories", [])
        if isinstance(configured, list) and configured:
            self.retryable_categories = {str(item) for item in configured}
        else:
            self.retryable_categories = {"tool_timeout", "temporary_network_error", "model_format_error"}

    def run(
        self,
        *,
        tool,
        payload: dict[str, object],
        execute: Callable[[], ToolResult],
    ) -> RetryOutcome:
        """Execute with retries and optional downgrade."""

        retry_errors: list[str] = []
        retries = max(int(getattr(tool.spec, "max_retries", 0)), 0)
        attempt = 0
        while True:
            result = execute()
            if result.success or result.failure_category not in self.retryable_categories or attempt >= retries:
                break
            retry_errors.append(str(result.error or result.failure_category or "retryable_failure"))
            attempt += 1
            self._sleep(attempt)

        if not result.success and result.failure_category in self.retryable_categories and attempt < retries:
            retry_errors.append(str(result.error or result.failure_category or "retryable_failure"))

        if not result.success and result.failure_category in self.retryable_categories and attempt >= retries:
            downgrade = getattr(tool, "downgrade", None)
            if callable(downgrade):
                downgraded = downgrade(payload, result)
                downgraded.metadata.setdefault("downgraded", True)
                downgraded.metadata.setdefault("downgrade_reason", "retry_exhausted")
                return RetryOutcome(
                    result=downgraded,
                    retry_count=attempt,
                    retry_errors=retry_errors or [str(result.error or result.failure_category)],
                    downgrade_reason="retry_exhausted",
                )

        return RetryOutcome(
            result=result,
            retry_count=attempt,
            retry_errors=retry_errors,
            downgrade_reason=None,
        )

    def _sleep(self, attempt: int) -> None:
        if self.base_delay_seconds <= 0:
            return
        if self.strategy == "exponential":
            delay = self.base_delay_seconds * (2 ** (attempt - 1))
        else:
            delay = self.base_delay_seconds
        time.sleep(delay)
