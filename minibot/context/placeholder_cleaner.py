"""Context cleanup helpers."""

from __future__ import annotations

import copy
import json


class PlaceholderCleaner:
    """Remove obvious placeholder artifacts and oversized tool outputs."""

    def __init__(self, max_tool_output_chars: int = 400) -> None:
        self.max_tool_output_chars = max_tool_output_chars

    def clean(self, text: str) -> str:
        """Keep the original text-cleaning interface for narrow callers."""

        return self._clean_text(text)[0]

    def clean_context(
        self, context: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
        """Clean structured context and return cleanup metadata for traces."""

        cleaned = copy.deepcopy(context)
        cleaned_placeholders: list[dict[str, object]] = []

        for key in ("memory", "history", "message"):
            value = str(cleaned.get(key, ""))
            cleaned_value, changes = self._clean_text(value)
            cleaned[key] = cleaned_value
            cleaned_placeholders.extend({"field": key, "kind": change} for change in changes)

        system_prompt = str(cleaned.get("system_prompt", ""))
        deduped_prompt, deduped = self._dedupe_lines(system_prompt)
        cleaned["system_prompt"] = deduped_prompt
        if deduped:
            cleaned_placeholders.append(
                {"field": "system_prompt", "kind": "duplicate_system_prompt"}
            )

        recalled: list[str] = []
        for item in cleaned.get("recalled_memories", []):
            cleaned_item, changes = self._clean_text(str(item))
            if cleaned_item.strip():
                recalled.append(cleaned_item)
            elif changes:
                cleaned_placeholders.append(
                    {"field": "recalled_memories", "kind": "empty_recalled_memory"}
                )
            cleaned_placeholders.extend(
                {"field": "recalled_memories", "kind": change} for change in changes
            )
        cleaned["recalled_memories"] = recalled

        cleaned["tool_results"], tool_changes = self._clean_tool_results(
            cleaned.get("tool_results", [])
        )
        cleaned_placeholders.extend(tool_changes)

        return cleaned, {"cleaned_placeholders": cleaned_placeholders}

    def _clean_text(self, text: str) -> tuple[str, list[str]]:
        changes: list[str] = []
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line in {"", "None", "<pending>", "TODO"}:
                if line:
                    changes.append(f"removed_{line.lower().replace('<', '').replace('>', '')}")
                continue
            cleaned_lines.append(
                raw_line.replace("<pending>", "").replace("TODO", "").replace("None", "").strip()
            )
        cleaned = "\n".join(line for line in cleaned_lines if line.strip())
        return cleaned, changes

    def _dedupe_lines(self, text: str) -> tuple[str, bool]:
        seen: set[str] = set()
        deduped_lines: list[str] = []
        removed = False
        for raw_line in text.splitlines():
            key = raw_line.strip()
            if key and key in seen:
                removed = True
                continue
            if key:
                seen.add(key)
            deduped_lines.append(raw_line)
        return "\n".join(deduped_lines).strip(), removed

    def _clean_tool_results(
        self, tool_results: object
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        if not isinstance(tool_results, list):
            return [], []
        cleaned_results: list[dict[str, object]] = []
        changes: list[dict[str, object]] = []
        for item in tool_results:
            if not isinstance(item, dict) or not item:
                changes.append({"field": "tool_results", "kind": "removed_empty_tool_result"})
                continue
            serialized = json.dumps(item, ensure_ascii=False)
            if len(serialized) > self.max_tool_output_chars:
                item = copy.deepcopy(item)
                item["output"] = self._truncate_value(item.get("output"))
                changes.append({"field": "tool_results", "kind": "truncated_tool_output"})
            cleaned_results.append(item)
        return cleaned_results, changes

    def _truncate_value(self, value: object) -> object:
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) <= self.max_tool_output_chars:
            return value
        return f"{serialized[: self.max_tool_output_chars]}..."
