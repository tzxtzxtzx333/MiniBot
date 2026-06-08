"""JSONL-backed task state store.

Each task is identified by a ``task_id``.  Updates append a new line to the
JSONL file; readers take the *last* record for a given ``task_id`` so that the
file is effectively a write-ahead log with last-write-wins semantics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_VALID_STATUSES = frozenset(
    {
        "pending",
        "running",
        "waiting_approval",
        "completed",
        "failed",
        "cancelled",
    }
)


class TaskStoreError(ValueError):
    """Raised when a task operation cannot be completed."""


class TaskStore:
    """Persistent task store backed by ``.minibot/tasks/tasks.jsonl``."""

    def __init__(self, tasks_dir: Path) -> None:
        self.root = tasks_dir
        self.tasks_file = tasks_dir / "tasks.jsonl"
        self._ensure()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def create(
        self,
        goal: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create a new task in ``pending`` status and return the record."""

        record: dict[str, object] = {
            "task_id": str(uuid4()),
            "status": "pending",
            "goal": goal,
            "created_at": self._now(),
            "updated_at": self._now(),
            "session_id": session_id,
            "user_id": user_id,
            "last_run_id": None,
            "pending_approval_id": None,
            "stop_reason": None,
            "metadata": metadata or {},
        }
        self._append(record)
        return record

    def list(
        self,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        """Return the most recent tasks (last-write-wins per ``task_id``).

        When *status* is provided only tasks matching that status are returned.
        """

        if limit < 1:
            return []

        latest = self._latest_snapshot()
        if status is not None:
            latest = [r for r in latest if r.get("status") == status]
        # Most recently updated first.
        latest.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        return latest[:limit]

    def get(self, task_id: str) -> dict[str, object] | None:
        """Return the latest record for *task_id*, or ``None``."""

        latest = self._latest_snapshot()
        for record in latest:
            if record.get("task_id") == task_id:
                return record
        return None

    def update(self, task_id: str, **fields: object) -> dict[str, object]:
        """Update one or more fields on *task_id* and persist a new revision.

        If *status* is included it is validated against the legal enum.
        """

        current = self.get(task_id)
        if current is None:
            raise TaskStoreError(f"task_not_found: {task_id}")

        if "status" in fields:
            status_value = str(fields["status"])
            if status_value not in _VALID_STATUSES:
                raise TaskStoreError(
                    f"invalid_status: {status_value} "
                    f"(allowed: {', '.join(sorted(_VALID_STATUSES))})"
                )

        updated = dict(current)
        updated.update(fields)
        updated["updated_at"] = self._now()
        self._append(updated)
        return updated

    def cancel(self, task_id: str) -> dict[str, object]:
        """Set *task_id* status to ``cancelled``."""

        return self.update(task_id, status="cancelled")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _latest_snapshot(self) -> list[dict[str, object]]:
        """Read all JSONL records and keep only the last entry per task_id."""

        all_records = self._read_all()
        seen: dict[str, dict[str, object]] = {}
        for record in all_records:
            tid = str(record.get("task_id", ""))
            if tid:
                seen[tid] = record
        return list(seen.values())

    def _read_all(self) -> list[dict[str, object]]:
        if not self.tasks_file.exists():
            return []
        records: list[dict[str, object]] = []
        try:
            content = self.tasks_file.read_text(encoding="utf-8-sig")
        except OSError:
            return []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records

    def _append(self, record: dict[str, object]) -> None:
        with self.tasks_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.tasks_file.exists():
            self.tasks_file.write_text("", encoding="utf-8")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
