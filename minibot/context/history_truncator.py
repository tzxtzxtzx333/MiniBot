"""History truncation helpers."""

from __future__ import annotations

from minibot.context.token_budget import TokenBudget


class HistoryTruncator:
    """Trim history text to a rough token budget while keeping the latest lines."""

    def __init__(self, token_budget: TokenBudget | None = None) -> None:
        self.token_budget = token_budget or TokenBudget()

    def truncate(self, text: str, max_tokens: int) -> str:
        lines = text.splitlines()
        kept: list[str] = []
        for line in reversed(lines):
            candidate = "\n".join(reversed([line, *kept]))
            if kept and self.token_budget.is_over_budget(candidate, max_tokens):
                break
            kept.insert(0, line)
        return "\n".join(kept)
