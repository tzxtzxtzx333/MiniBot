"""Relevance-based HISTORY.md retrieval for context injection."""

from __future__ import annotations

import re


class HistoryRetriever:
    """Score HISTORY.md entries by relevance to the current query and return top_k."""

    _token_pattern = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")

    def __init__(
        self,
        *,
        enabled: bool = True,
        mode: str = "relevance",
        top_k: int = 5,
        max_chars: int = 2000,
    ) -> None:
        self.enabled = enabled
        self.mode = mode
        self.top_k = top_k
        self.max_chars = max_chars

    def retrieve(self, query: str, history_text: str) -> dict[str, object]:
        """Return relevant history segments and retrieval metadata.

        When disabled or history is empty, returns the full history text unchanged
        with a ``history_retrieval_mode`` of ``"full"``.
        """
        if not self.enabled:
            return {
                "history_text": history_text,
                "history_retrieval_mode": "full",
                "retrieved_history_count": 0,
                "retrieved_history_chars": len(history_text),
            }

        query_terms = self._tokenize(query)
        if not query_terms:
            return {
                "history_text": history_text,
                "history_retrieval_mode": "relevance",
                "retrieved_history_count": 0,
                "retrieved_history_chars": 0,
            }

        turns = self._parse_turns(history_text)
        if not turns:
            return {
                "history_text": "",
                "history_retrieval_mode": "relevance",
                "retrieved_history_count": 0,
                "retrieved_history_chars": 0,
            }

        scored = self._score_turns(turns, query_terms, query)
        if not scored:
            return {
                "history_text": "",
                "history_retrieval_mode": "relevance",
                "retrieved_history_count": 0,
                "retrieved_history_chars": 0,
            }

        ranked = sorted(scored, key=lambda item: -item[0])
        top = ranked[: self.top_k]

        retrieved_text = "\n".join(item[1] for item in top)
        retrieved_chars = len(retrieved_text)

        if self.max_chars > 0 and retrieved_chars > self.max_chars:
            retrieved_text = retrieved_text[: self.max_chars]

        return {
            "history_text": retrieved_text,
            "history_retrieval_mode": "relevance",
            "retrieved_history_count": len(top),
            "retrieved_history_chars": min(retrieved_chars, self.max_chars),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_turns(self, history_text: str) -> list[tuple[str, str]]:
        """Split raw history into (user_line, assistant_line) turn pairs."""
        lines = history_text.splitlines()
        turns: list[tuple[str, str]] = []
        pending_user: str | None = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("user: "):
                if pending_user is not None:
                    turns.append((pending_user, ""))
                pending_user = line[6:].strip()
            elif line.startswith("assistant: "):
                assistant = line[11:].strip()
                if pending_user is not None:
                    turns.append((pending_user, assistant))
                    pending_user = None
                else:
                    turns.append(("", assistant))
            elif pending_user is not None:
                # continuation line after a user: prefix
                pending_user += " " + line
        if pending_user is not None:
            turns.append((pending_user, ""))
        return turns

    def _score_turns(
        self,
        turns: list[tuple[str, str]],
        query_terms: set[str],
        query: str,
    ) -> list[tuple[int, str]]:
        """Score each turn and return (score, turn_text) pairs with score > 0."""
        query_lower = query.lower()
        results: list[tuple[int, str]] = []
        for user, assistant in turns:
            combined = f"user: {user}\nassistant: {assistant}"
            score = self._score_line(combined, query_terms)
            # Exact phrase bonus: +3 if the full query appears as a substring
            if query_lower and query_lower in combined.lower():
                score += 3
            if score > 0:
                results.append((score, combined))
        return results

    def _score_line(self, text: str, query_terms: set[str]) -> int:
        """Token overlap score."""
        lowered = text.lower()
        tokens = self._tokenize(text)
        # Overlap: tokens in text that also appear in query
        score = sum(1 for token in tokens if token in query_terms)
        # Substring bonus: query terms appearing anywhere in the text
        score += sum(1 for term in query_terms if term and term in lowered)
        return score

    def _tokenize(self, text: str) -> set[str]:
        return {match.group(0).lower() for match in self._token_pattern.finditer(text)}
