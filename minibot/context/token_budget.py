"""Simple token-budget estimation helpers."""

from __future__ import annotations


class TokenBudget:
    """Track rough token budgets using a character-based estimate."""

    def estimate_text(self, text: str) -> int:
        normalized = text.strip()
        if not normalized:
            return 0
        return max(len(normalized) // 4, 1)

    def remaining(self, text: str, limit: int) -> int:
        return max(limit - self.estimate_text(text), 0)

    def is_over_budget(self, text: str, limit: int) -> bool:
        return self.estimate_text(text) > limit
