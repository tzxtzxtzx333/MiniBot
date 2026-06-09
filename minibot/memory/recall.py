"""Relevant-memory recall helpers."""

from __future__ import annotations

import re
from pathlib import Path


class MemoryRecall:
    """Recall relevant snippets from recent history and archived summaries."""

    _token_pattern = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")

    def recall(
        self,
        query: str,
        memory_text: str = "",
        history_text: str = "",
        archives_dir: Path | None = None,
        *,
        max_items: int = 6,
    ) -> list[str]:
        """Return relevant snippets from recent and archived context."""

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        candidates: list[tuple[int, str]] = []
        candidates.extend(self._collect_scored_lines(memory_text, query_tokens, prefix="memory"))
        candidates.extend(self._collect_scored_lines(history_text, query_tokens, prefix="history"))

        if archives_dir is not None and archives_dir.exists():
            for archive_path in sorted(archives_dir.glob("*.md")):
                try:
                    archive_text = archive_path.read_text(encoding="utf-8")
                except (PermissionError, UnicodeDecodeError, OSError, IsADirectoryError):
                    continue
                candidates.extend(
                    self._collect_scored_lines(
                        archive_text, query_tokens, prefix=f"archive:{archive_path.name}"
                    )
                )

        ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
        unique: list[str] = []
        for _, snippet in ranked:
            if snippet not in unique:
                unique.append(snippet)
            if len(unique) >= max_items:
                break
        return unique

    def _collect_scored_lines(
        self, text: str, query_tokens: set[str], *, prefix: str
    ) -> list[tuple[int, str]]:
        scored: list[tuple[int, str]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            score = self._score(line, query_tokens)
            if score > 0:
                scored.append((score, f"[{prefix}] {line}"))
        return scored

    def _score(self, text: str, query_tokens: set[str]) -> int:
        lowered = text.lower()
        tokens = self._tokenize(text)
        score = sum(1 for token in tokens if token in query_tokens)
        score += sum(1 for token in query_tokens if token and token in lowered)
        return score

    def _tokenize(self, text: str) -> set[str]:
        return {match.group(0).lower() for match in self._token_pattern.finditer(text)}
