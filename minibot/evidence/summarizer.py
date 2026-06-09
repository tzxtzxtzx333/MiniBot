"""Evidence summarizer — compress large tool outputs for evidence injection."""

from __future__ import annotations

import re


class EvidenceSummarizer:
    """Generate summaries and key points from raw tool outputs.

    Fake mode uses rule-based truncation.  Real mode can delegate to an
    external summarizer agent when available.
    """

    _sentence_boundary = re.compile(r"[。！？.!?\n]")

    def __init__(
        self,
        *,
        mode: str = "fake",
        summary_max_chars: int = 800,
        key_points_max: int = 5,
        external_summarizer=None,
    ) -> None:
        self.mode = mode
        self.summary_max_chars = summary_max_chars
        self.key_points_max = key_points_max
        self._external_summarizer = external_summarizer

    def summarize(self, tool_name: str, raw_output: object) -> dict[str, object]:
        """Produce ``summary``, ``key_points``, and ``raw_chars`` for a tool output."""
        text = self._normalize_output(raw_output)
        raw_chars = len(text)

        if self.mode == "real" and self._external_summarizer is not None:
            try:
                return self._real_summarize(tool_name, text, raw_chars)
            except Exception:
                # Fall back to fake on any failure
                pass

        return self._fake_summarize(tool_name, text, raw_chars)

    # ------------------------------------------------------------------
    # Fake / rule-based
    # ------------------------------------------------------------------

    def _fake_summarize(self, tool_name: str, text: str, raw_chars: int) -> dict[str, object]:
        summary = self._truncate_text(text, self.summary_max_chars)
        key_points = self._extract_key_points(text, self.key_points_max)
        return {
            "summary": summary,
            "key_points": key_points,
            "raw_chars": raw_chars,
            "archive_mode": "fake",
        }

    # ------------------------------------------------------------------
    # Real
    # ------------------------------------------------------------------

    def _real_summarize(self, tool_name: str, text: str, raw_chars: int) -> dict[str, object]:
        summarizer = self._external_summarizer
        result = summarizer.summarize(history_text=f"[{tool_name} output]\n{text}", memory_text="")
        summary = str(result.get("summary", ""))
        key_points_text = summary[: self.summary_max_chars]
        key_points = self._extract_key_points(text, self.key_points_max)
        return {
            "summary": key_points_text,
            "key_points": key_points,
            "raw_chars": raw_chars,
            "archive_mode": str(result.get("archive_mode", "real")),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_output(raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        import json

        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(raw)

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        # Reserve room for the ellipsis
        limit = max(max_chars - 1, 1)
        truncated = text[:limit]
        # Try to break at a sentence boundary
        last_boundary = 0
        for match in self._sentence_boundary.finditer(truncated):
            last_boundary = match.end()
        if last_boundary > limit // 2:
            result = truncated[:last_boundary]
        else:
            result = truncated.rstrip()
        return result + "…"

    def _extract_key_points(self, text: str, max_points: int) -> list[str]:
        """Extract salient lines as key points (rule-based)."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # Prefer lines that look like bullet points or headings
        bullet_candidates = [line for line in lines if re.match(r"^[-*#]|^\d+[.)]", line)]
        if bullet_candidates:
            return bullet_candidates[:max_points]
        # Otherwise pick the longest substantive lines
        substantive = [line for line in lines if len(line) > 20]
        substantive.sort(key=len, reverse=True)
        return substantive[:max_points]
