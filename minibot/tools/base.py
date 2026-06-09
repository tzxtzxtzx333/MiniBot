"""Tool protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolSpec:
    """Declarative tool contract."""

    name: str
    description: str
    input_schema: dict[str, object]
    risk_level: str
    sandbox_required: bool
    timeout: int
    max_retries: int


@dataclass(slots=True)
class ToolResult:
    """Structured tool execution result."""

    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    failure_category: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    status_override: str | None = None

    @property
    def status(self) -> str:
        if self.status_override is not None:
            return self.status_override
        return "success" if self.success else "failed"

    def to_result_record(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "failure_category": self.failure_category,
            "metadata": dict(self.metadata),
        }

    def to_trace_record(self, arguments: dict[str, object]) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(arguments),
            "status": self.status,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "failure_category": self.failure_category,
            "metadata": dict(self.metadata),
        }


class ToolError(Exception):
    """Structured tool-layer exception."""

    def __init__(self, message: str, failure_category: str) -> None:
        super().__init__(message)
        self.failure_category = failure_category


class ToolNotFoundError(ToolError):
    """Raised when a tool is not registered."""


class ToolValidationError(ToolError):
    """Raised when tool input does not satisfy schema."""


class BaseTool:
    """Base class for concrete tool implementations."""

    spec: ToolSpec

    def handle(self, payload: dict[str, object]) -> Any:
        raise NotImplementedError

    def execute(self, payload: dict[str, object]) -> ToolResult:
        try:
            output = self.handle(payload)
        except ToolError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=str(exc),
                failure_category=exc.failure_category,
                metadata={},
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output=output,
            error=None,
            failure_category=None,
            metadata={},
        )


def blocked_tool_result(
    tool_name: str,
    *,
    error: str,
    failure_category: str,
    metadata: dict[str, object] | None = None,
) -> ToolResult:
    """Create a blocked tool result without treating it as success."""

    return ToolResult(
        tool_name=tool_name,
        success=False,
        output=None,
        error=error,
        failure_category=failure_category,
        metadata=dict(metadata or {}),
        status_override="blocked",
    )


def provider_metadata(
    *,
    provider: str,
    provider_status: str,
    mock_provider: bool,
    real_provider: bool,
    mcp_provider: bool = False,
    **extra: object,
) -> dict[str, object]:
    """Build a standardized provider status metadata dict for tool results."""
    meta: dict[str, object] = {
        "provider": provider,
        "provider_status": provider_status,
        "mock_provider": mock_provider,
        "real_provider": real_provider,
        "mcp_provider": mcp_provider,
    }
    meta.update(extra)
    return meta
