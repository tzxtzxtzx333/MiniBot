"""Lightweight tool orchestration agent."""

from __future__ import annotations

from minibot.harness.model_client import ModelPlan


class ToolAgent:
    """Extract model tool calls and execute them through the shared dispatcher."""

    def __init__(self, tool_dispatcher) -> None:
        self.tool_dispatcher = tool_dispatcher

    def extract_tool_calls(self, plan: ModelPlan) -> list[dict[str, object]]:
        """Normalize tool calls from one model plan."""

        normalized: list[dict[str, object]] = []
        for call in plan.tool_calls:
            if hasattr(call, "to_trace"):
                raw_call = call.to_trace()
            elif isinstance(call, dict):
                raw_call = {
                    "tool_name": call.get("tool_name"),
                    "arguments": call.get("arguments"),
                }
            else:
                raw_call = {
                    "tool_name": None,
                    "arguments": None,
                }
            normalized.append(self._normalize_tool_call(raw_call))
        return normalized

    def execute_plan(self, plan: ModelPlan, dispatch_context: dict[str, object] | None = None) -> dict[str, object]:
        """Validate and dispatch the tool calls declared by the model plan."""

        tool_calls = self.extract_tool_calls(plan)
        valid_tool_calls = [call for call in tool_calls if call.get("failure_category") != "invalid_tool_call"]
        invalid_tool_calls = [call for call in tool_calls if call.get("failure_category") == "invalid_tool_call"]
        tool_results = [self._invalid_tool_result(call) for call in invalid_tool_calls]
        tool_trace = [self._invalid_tool_trace(call) for call in invalid_tool_calls]
        if valid_tool_calls:
            dispatched_results, dispatched_trace = self.tool_dispatcher.dispatch(valid_tool_calls, dispatch_context=dispatch_context)
            tool_results.extend(dispatched_results)
            tool_trace.extend(dispatched_trace)
        return {
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "tool_trace": tool_trace,
            "dispatcher_metadata": dict(self.tool_dispatcher.last_execution_metadata),
        }

    @staticmethod
    def _normalize_tool_call(raw_call: dict[str, object]) -> dict[str, object]:
        tool_name = raw_call.get("tool_name")
        arguments = raw_call.get("arguments")
        if isinstance(tool_name, str) and isinstance(arguments, dict):
            return {"tool_name": tool_name, "arguments": dict(arguments)}
        normalized_name = tool_name if isinstance(tool_name, str) and tool_name else "invalid_tool_call"
        return {
            "tool_name": normalized_name,
            "arguments": dict(arguments) if isinstance(arguments, dict) else {},
            "failure_category": "invalid_tool_call",
            "error": "invalid_tool_call",
        }

    @staticmethod
    def _invalid_tool_result(call: dict[str, object]) -> dict[str, object]:
        return {
            "tool_name": call["tool_name"],
            "status": "failed",
            "success": False,
            "output": None,
            "error": str(call.get("error", "invalid_tool_call")),
            "failure_category": "invalid_tool_call",
            "metadata": {},
        }

    @staticmethod
    def _invalid_tool_trace(call: dict[str, object]) -> dict[str, object]:
        return {
            "tool_name": call["tool_name"],
            "arguments": dict(call.get("arguments", {})),
            "status": "failed",
            "success": False,
            "output": None,
            "error": str(call.get("error", "invalid_tool_call")),
            "failure_category": "invalid_tool_call",
            "metadata": {},
        }
