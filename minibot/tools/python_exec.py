"""High-risk Python execution tool placeholder."""

from __future__ import annotations

from .base import BaseTool, ToolResult, ToolSpec


class PythonExecTool(BaseTool):
    """Register Python execution through the tool protocol without host execution."""

    spec = ToolSpec(
        name="python_exec",
        description="Execute Python code in a sandbox executor.",
        input_schema={
            "type": "object",
            "required": ["code"],
            "additionalProperties": False,
            "properties": {"code": {"type": "string"}},
        },
        risk_level="high",
        sandbox_required=True,
        timeout=30,
        max_retries=0,
    )

    def execute(self, payload: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            output=None,
            error="requires_sandbox_executor",
            failure_category="sandbox_required",
            metadata={"sandbox_required": True},
        )
