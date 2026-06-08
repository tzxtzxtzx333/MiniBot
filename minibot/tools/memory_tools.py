"""Memory-connected tools."""

from __future__ import annotations

from datetime import datetime, timezone

from .base import BaseTool, ToolSpec


class MemorySearchTool(BaseTool):
    """Search MEMORY.md and HISTORY.md for relevant content."""

    spec = ToolSpec(
        name="memory_search",
        description="Search long-term and recent memory for a keyword.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
        },
        risk_level="low",
        sandbox_required=False,
        timeout=5,
        max_retries=0,
    )

    def __init__(self, workspace, memory_recall) -> None:
        self.workspace = workspace
        self.memory_recall = memory_recall

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        query = str(payload["query"]).strip()
        results = self.memory_recall.recall(
            query,
            memory_text=self.workspace.read_memory(),
            history_text=self.workspace.read_history(),
            archives_dir=self.workspace.archives_dir,
        )
        return {"query": query, "results": results}


class MemoryWriteTool(BaseTool):
    """Write structured memory entries."""

    spec = ToolSpec(
        name="memory_write",
        description="Write a structured long-term memory entry.",
        input_schema={
            "type": "object",
            "required": ["content"],
            "additionalProperties": False,
            "properties": {"content": {"type": "string"}},
        },
        risk_level="high",
        sandbox_required=False,
        timeout=5,
        max_retries=0,
    )

    def __init__(self, memory_store) -> None:
        self.memory_store = memory_store

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        content = str(payload["content"]).strip()
        written = self.memory_store.write_memory_fact(content, include_timestamp=True)
        return {
            "content": content,
            "written": written,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
