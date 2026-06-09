"""Lightweight memory routing agent."""

from __future__ import annotations

import re


class MemoryAgent:
    """Classify whether a user turn should also persist to long-term memory."""

    _remember_pattern = re.compile(r"(?:记住|remember)\s*[:：]?\s*(.+)", re.IGNORECASE)

    def assess(self, content: str) -> dict[str, object]:
        """Return a structured persistence decision for one user message."""

        normalized = content.strip()
        match = self._remember_pattern.search(normalized)
        if match is None:
            return {
                "store_memory": False,
                "store_history": True,
                "memory_fact": None,
                "reason": "history_only",
            }
        fact = match.group(1).strip().strip('“”"')
        return {
            "store_memory": bool(fact),
            "store_history": True,
            "memory_fact": fact or None,
            "reason": "explicit_memory_request" if fact else "empty_memory_request",
        }
