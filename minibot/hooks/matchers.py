"""Hook matcher helpers."""

from __future__ import annotations

import re
from typing import Protocol


class HookMatcher(Protocol):
    """Protocol for hook matchers."""

    def matches(self, pattern: str, value: str) -> bool: ...


class ExactMatcher:
    """Match an event payload by exact text equality."""

    def matches(self, pattern: str, value: str) -> bool:
        return pattern == value


class RegexMatcher:
    """Match an event payload using a regular expression."""

    def matches(self, pattern: str, value: str) -> bool:
        return re.search(pattern, value) is not None
