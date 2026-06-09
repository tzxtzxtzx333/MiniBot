"""Workspace-constrained file tools."""

from __future__ import annotations

from pathlib import Path, PurePath

from .base import BaseTool, ToolError, ToolSpec


class _WorkspacePathMixin:
    def __init__(self, allowed_root: Path) -> None:
        self.allowed_root = allowed_root.resolve()

    def _resolve_path(self, raw_path: str) -> Path:
        cleaned = raw_path.strip()
        if not cleaned:
            raise ToolError("invalid_path", "invalid_path")
        candidate = PurePath(cleaned)
        if candidate.is_absolute():
            raise ToolError("absolute path blocked by policy", "blocked_by_policy")
        if any(part == ".." for part in candidate.parts):
            raise ToolError("path escape blocked by policy", "blocked_by_policy")
        target = (self.allowed_root / Path(*candidate.parts)).resolve()
        if self.allowed_root not in target.parents and target != self.allowed_root:
            raise ToolError("path escape blocked by policy", "blocked_by_policy")
        return target


class FileReadTool(_WorkspacePathMixin, BaseTool):
    """Read UTF-8 files inside the allowed sandbox workspace root."""

    spec = ToolSpec(
        name="file_read",
        description="Read a text file from the sandbox workspace.",
        input_schema={
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=5,
        max_retries=0,
    )

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        target = self._resolve_path(str(payload["path"]))
        if not target.exists():
            raise ToolError("file_not_found", "file_not_found")
        if not target.is_file():
            raise ToolError("not_a_file", "file_not_found")
        return {
            "path": str(target),
            "content": target.read_text(encoding="utf-8"),
        }


class FileWriteTool(_WorkspacePathMixin, BaseTool):
    """Write UTF-8 files inside the allowed sandbox workspace root."""

    spec = ToolSpec(
        name="file_write",
        description="Write a text file under the sandbox workspace.",
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        risk_level="high",
        sandbox_required=False,
        timeout=5,
        max_retries=0,
    )

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        target = self._resolve_path(str(payload["path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(payload["content"])
        target.write_text(content, encoding="utf-8")
        return {
            "path": str(target),
            "bytes_written": len(content.encode("utf-8")),
        }
