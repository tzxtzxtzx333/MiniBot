"""JSONL-backed evidence store for tool output offloading."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class EvidenceStore:
    """Persist compressed tool outputs as structured evidence records.

    Each record is appended as one line in ``evidence.jsonl`` inside the
    workspace evidence directory.
    """

    _token_pattern = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")

    def __init__(self, evidence_dir: Path) -> None:
        self._dir = evidence_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._jsonl = self._dir / "evidence.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        run_id: str,
        task_id: str | None,
        tool_name: str,
        source: str,
        raw_chars: int,
        summary: str,
        key_points: list[str],
        raw_ref: str = "",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Append a new evidence record and return it."""
        evidence_id = f"ev_{uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc).isoformat()
        record: dict[str, object] = {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "task_id": task_id,
            "tool_name": tool_name,
            "source": source,
            "raw_chars": raw_chars,
            "summary": summary,
            "key_points": key_points,
            "raw_ref": raw_ref,
            "created_at": created_at,
            "metadata": dict(metadata or {}),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._jsonl.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return record

    def get(self, evidence_id: str) -> dict[str, object] | None:
        """Return a single evidence record by id."""
        for record in self._read_all():
            if record.get("evidence_id") == evidence_id:
                return record
        return None

    def list(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Return records filtered by *run_id* and/or *task_id*."""
        if not self._jsonl.exists():
            return []
        results: list[dict[str, object]] = []
        for record in self._read_all():
            if run_id is not None and record.get("run_id") != run_id:
                continue
            if task_id is not None and record.get("task_id") != task_id:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def search(self, query: str, top_k: int = 5) -> list[dict[str, object]]:
        """Keyword-based relevance search over evidence records."""
        if not self._jsonl.exists():
            return []
        query_terms = self._tokenize(query)
        if not query_terms:
            return []
        scored: list[tuple[int, dict[str, object]]] = []
        for record in self._read_all():
            score = self._score(record, query_terms, query)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: -item[0])
        return [item[1] for item in scored[:top_k]]

    def count(self) -> int:
        """Return the total number of evidence records."""
        if not self._jsonl.exists():
            return 0
        return sum(1 for _ in self._read_all())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_all(self):
        if not self._jsonl.exists():
            return
        with self._jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def _score(self, record: dict[str, object], query_terms: set[str], query: str) -> int:
        searchable = " ".join(
            [
                str(record.get("tool_name", "")),
                str(record.get("source", "")),
                str(record.get("summary", "")),
                " ".join(str(kp) for kp in record.get("key_points", [])),
            ]
        )
        lowered = searchable.lower()
        query_lower = query.lower()
        score = sum(1 for term in query_terms if term in lowered)
        if query_lower and query_lower in lowered:
            score += 3
        return score

    def _tokenize(self, text: str) -> set[str]:
        return {match.group(0).lower() for match in self._token_pattern.finditer(text)}
