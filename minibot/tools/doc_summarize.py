"""Document summarization tool."""

from __future__ import annotations

from pathlib import Path

from .base import BaseTool, ToolError, ToolSpec


class DocSummarizeTool(BaseTool):
    """Generate a simple extractive summary from text or a workspace file."""

    spec = ToolSpec(
        name="doc_summarize",
        description="Summarize input text or a text file from the workspace.",
        input_schema={
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "path": {"type": "string"},
            },
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=10,
        max_retries=0,
    )

    def __init__(self, allowed_root: Path) -> None:
        self.allowed_root = allowed_root.resolve()

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("text", "")).strip()
        path = str(payload.get("path", "")).strip()
        if not text and not path:
            raise ToolError("text_or_path_required", "schema_validation_failed")
        source_text = text if text else self._read_path(path)
        summary = self._summarize(source_text)
        return {
            "summary": summary,
            "source_length": len(source_text),
        }

    def _read_path(self, raw_path: str) -> str:
        candidate = Path(raw_path)
        target = (self.allowed_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if self.allowed_root not in target.parents and target != self.allowed_root:
            raise ToolError("path_not_allowed", "path_not_allowed")
        if not target.exists():
            raise ToolError("file_not_found", "file_not_found")
        return target.read_text(encoding="utf-8")

    def _summarize(self, text: str) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= 120:
            return f"Summary: {normalized}"
        head = normalized[:120].rstrip()
        return f"Summary: {head}..."
