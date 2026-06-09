"""Duplicate tool call detection helpers."""

from __future__ import annotations

import json


class DuplicateCallDetector:
    """Deduplicate identical tool calls inside one dispatch batch."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._results_by_signature: dict[str, dict[str, object]] = {}

    def reset(self) -> None:
        """Clear per-dispatch duplicate state."""

        self._results_by_signature.clear()

    def signature(self, tool_name: str, arguments: dict[str, object]) -> str:
        """Build a stable dedupe signature."""

        return json.dumps(
            {"tool_name": tool_name, "arguments": arguments}, ensure_ascii=False, sort_keys=True
        )

    def lookup(self, signature: str) -> dict[str, object] | None:
        """Return the previous result for an identical call, if present."""

        if not self.enabled:
            return None
        cached = self._results_by_signature.get(signature)
        if cached is None:
            return None
        return json.loads(json.dumps(cached, ensure_ascii=False))

    def remember(self, signature: str, result: dict[str, object]) -> None:
        """Cache one tool result for later duplicate hits."""

        if not self.enabled:
            return
        self._results_by_signature[signature] = json.loads(json.dumps(result, ensure_ascii=False))
