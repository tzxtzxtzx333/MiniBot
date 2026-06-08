"""Sensitive data redaction helpers for governance and tracing."""

from __future__ import annotations

import re
from typing import Any


class SensitiveInfoRedactor:
    """Redact obvious secrets from strings and nested structures."""

    _SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "authorization"}

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns = [re.compile(pattern, re.IGNORECASE) for pattern in (patterns or [])]
        if self._patterns:
            return
        self._patterns = [
            re.compile(r"sk-[A-Za-z0-9_-]+", re.IGNORECASE),
            re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
            re.compile(r"\b(api[_-]?key|token|secret|password)\b\s*([=:])\s*([^\s,;，；]+)", re.IGNORECASE),
            re.compile(r"\b(api[_-]?key|token|secret|password)\b\s+(is)\s+([^\s,;，；]+)", re.IGNORECASE),
            re.compile(r"\b(api[_-]?key|token|secret|password)\b\s*(是)\s*([^\s,;，；]+)", re.IGNORECASE),
        ]

    def redact_value(self, value: Any) -> tuple[Any, list[str]]:
        """Redact a nested value and return touched field paths."""

        return self._redact_recursive(value, prefix="")

    def _redact_recursive(self, value: Any, *, prefix: str) -> tuple[Any, list[str]]:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            fields: list[str] = []
            for key, item in value.items():
                key_path = f"{prefix}.{key}" if prefix else key
                if key.lower() in self._SENSITIVE_KEYS:
                    redacted[key] = "[REDACTED]"
                    fields.append(key_path)
                    continue
                redacted_item, item_fields = self._redact_recursive(item, prefix=key_path)
                redacted[key] = redacted_item
                fields.extend(item_fields)
            return redacted, fields
        if isinstance(value, list):
            redacted_items: list[Any] = []
            fields: list[str] = []
            for index, item in enumerate(value):
                item_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
                redacted_item, item_fields = self._redact_recursive(item, prefix=item_prefix)
                redacted_items.append(redacted_item)
                fields.extend(item_fields)
            return redacted_items, fields
        if isinstance(value, str):
            return self._redact_string(value, prefix or "value")
        return value, []

    def _redact_string(self, text: str, field_name: str) -> tuple[str, list[str]]:
        updated = text
        touched = False
        for pattern in self._patterns:
            if pattern.search(updated):
                updated = pattern.sub(self._replace_match, updated)
                touched = True
        return (updated, [field_name]) if touched else (updated, [])

    @staticmethod
    def _replace_match(match: re.Match[str]) -> str:
        full = match.group(0)
        if full.lower().startswith("bearer "):
            return "Bearer [REDACTED]"
        if full.lower().startswith("sk-"):
            return "[REDACTED]"
        if match.lastindex == 3:
            label = match.group(1)
            separator = match.group(2)
            return f"{label} {separator} [REDACTED]" if separator.lower() == "is" else f"{label}{separator}[REDACTED]"
        return "[REDACTED]"
