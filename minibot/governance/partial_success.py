"""Partial success evaluation for multi-tool runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PartialSuccessSummary:
    """Summary of mixed tool execution results."""

    partial_success: bool
    success_tools: list[str]
    failed_tools: list[str]
    message: str | None


class PartialSuccessHandler:
    """Detect and summarize mixed success/failure outcomes."""

    def evaluate(self, results: list[dict[str, object]]) -> PartialSuccessSummary:
        success_tools = [
            str(item["tool_name"]) for item in results if item.get("status") == "success"
        ]
        failed_tools = [
            str(item["tool_name"])
            for item in results
            if item.get("status") in {"failed", "blocked"}
        ]
        partial_success = bool(success_tools and failed_tools)
        message = None
        if partial_success:
            message = f"completed {', '.join(success_tools)}; " f"failed {', '.join(failed_tools)}"
        return PartialSuccessSummary(
            partial_success=partial_success,
            success_tools=success_tools,
            failed_tools=failed_tools,
            message=message,
        )
