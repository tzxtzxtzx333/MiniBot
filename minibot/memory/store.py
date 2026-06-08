"""Memory and recent-history persistence helpers."""

from __future__ import annotations

import re

from minibot.channels.base import ChannelMessage
from minibot.context.token_budget import TokenBudget


class MemoryStore:
    """Persist recent dialogue, long-term memory, and archive-trigger decisions."""

    _remember_pattern = re.compile(r"(?:记住|remember)\s*[:：]?\s*(.+)", re.IGNORECASE)

    def __init__(
        self,
        workspace,
        compactor=None,
        token_budget: TokenBudget | None = None,
    ) -> None:
        self.workspace = workspace
        self.compactor = compactor
        self.token_budget = token_budget or TokenBudget()

    def append_history(self, message: ChannelMessage, response: str) -> None:
        """Persist the latest turn into `HISTORY.md` and a session transcript."""

        self.workspace.append_history(f"user: {message.content}")
        self.workspace.append_history(f"assistant: {response}")
        self.workspace.append_session(message.session_id, f"user: {message.content}")
        self.workspace.append_session(message.session_id, f"assistant: {response}")

    def remember_if_requested(self, message: ChannelMessage) -> list[str]:
        """Persist explicit long-term memory directives from the user."""

        match = self._remember_pattern.search(message.content.strip())
        if match is None:
            return []
        fact = match.group(1).strip()
        if not fact:
            return []
        return [fact] if self.write_memory_fact(fact, include_timestamp=False) else []

    def write_memory_fact(self, fact: str, *, include_timestamp: bool) -> bool:
        """Write one memory item if it does not already exist."""

        normalized_fact = fact.strip()
        if not normalized_fact:
            return False
        existing_items = self._read_memory_items()
        if normalized_fact in existing_items:
            return False
        prefix = ""
        if include_timestamp:
            from datetime import datetime, timezone

            prefix = f"[{datetime.now(timezone.utc).isoformat()}] "
        self.workspace.append_memory(f"- {prefix}{normalized_fact}")
        return True

    def turn_count(self) -> int:
        """Return the number of recent dialogue turns in `HISTORY.md`."""

        history_lines = self.workspace.read_history().splitlines()
        return sum(1 for line in history_lines if line.startswith("user: "))

    def history_token_count(self) -> int:
        """Estimate the current token load of the recent-history window."""

        return self.token_budget.estimate_text(self.workspace.read_history())

    def compact_history(
        self,
        *,
        source_session_id: str,
        compression_trigger: str,
    ) -> dict[str, object] | None:
        """Archive the recent history window and reset `HISTORY.md`."""

        if self.compactor is None:
            return None
        history_text = self.workspace.read_history()
        if self._is_effectively_empty_history(history_text):
            return None
        result = self.compactor.compact(
            source_session_id=source_session_id,
            history_text=history_text,
            memory_text=self.workspace.read_memory(),
            compression_trigger=compression_trigger,
        )
        self.workspace.reset_history()
        return result

    @staticmethod
    def _is_effectively_empty_history(history_text: str) -> bool:
        content_lines = [
            line.strip()
            for line in history_text.splitlines()
            if line.strip() and not line.strip().startswith("# HISTORY")
        ]
        return not content_lines

    def _read_memory_items(self) -> list[str]:
        memory_lines = self.workspace.read_memory().splitlines()
        items: list[str] = []
        for line in memory_lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                item = stripped[2:].strip()
                if item.startswith("[") and "] " in item:
                    item = item.split("] ", 1)[1].strip()
                items.append(item)
        return items
