"""Persistent pending/resolved approval queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class ApprovalStoreError(ValueError):
    """Raised when an approval operation cannot be completed."""


@dataclass(slots=True)
class ApprovalStorePaths:
    root: Path
    pending_file: Path
    resolved_file: Path


class ApprovalStore:
    """JSONL-backed approval queue for graylisted tool calls."""

    def __init__(self, approvals_dir: Path) -> None:
        self.root = approvals_dir
        self.pending_file = approvals_dir / "pending.jsonl"
        self.resolved_file = approvals_dir / "resolved.jsonl"
        self._ensure_files()

    @property
    def paths(self) -> ApprovalStorePaths:
        return ApprovalStorePaths(
            root=self.root,
            pending_file=self.pending_file,
            resolved_file=self.resolved_file,
        )

    def create_pending(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        arguments: dict[str, object],
        risk_level: str,
        reason: str,
    ) -> dict[str, object]:
        signature = self.build_request_signature(user_id=user_id, tool_name=tool_name, arguments=arguments)
        existing = self.find_pending_by_signature(signature)
        if existing is not None:
            return existing
        record = {
            "approval_id": str(uuid4()),
            "created_at": self._now(),
            "session_id": session_id,
            "user_id": user_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "risk_level": risk_level,
            "reason": reason,
            "status": "pending",
            "request_signature": signature,
        }
        self._append_jsonl(self.pending_file, record)
        return record

    def list_pending(self) -> list[dict[str, object]]:
        return [record for record in self._read_jsonl(self.pending_file) if str(record.get("status")) == "pending"]

    def approve(self, approval_id: str) -> dict[str, object]:
        return self._resolve(approval_id, action="approved")

    def reject(self, approval_id: str) -> dict[str, object]:
        return self._resolve(approval_id, action="rejected")

    def find_resolution(self, *, user_id: str, tool_name: str, arguments: dict[str, object]) -> dict[str, object] | None:
        signature = self.build_request_signature(user_id=user_id, tool_name=tool_name, arguments=arguments)
        resolved = [
            record
            for record in self._read_jsonl(self.resolved_file)
            if str(record.get("request_signature")) == signature
        ]
        return resolved[-1] if resolved else None

    def counts(self) -> dict[str, int]:
        pending = self.list_pending()
        resolved = self._read_jsonl(self.resolved_file)
        approved = sum(1 for item in resolved if str(item.get("status")) == "approved")
        rejected = sum(1 for item in resolved if str(item.get("status")) == "rejected")
        return {
            "pending_count": len(pending),
            "approved_count": approved,
            "rejected_count": rejected,
        }

    @staticmethod
    def build_request_signature(*, user_id: str, tool_name: str, arguments: dict[str, object]) -> str:
        canonical = json.dumps(
            {
                "user_id": user_id,
                "tool_name": tool_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def find_pending_by_signature(self, signature: str) -> dict[str, object] | None:
        pending = [
            record
            for record in self._read_jsonl(self.pending_file)
            if str(record.get("request_signature")) == signature and str(record.get("status")) == "pending"
        ]
        return pending[-1] if pending else None

    def _resolve(self, approval_id: str, *, action: str) -> dict[str, object]:
        pending = self._read_jsonl(self.pending_file)
        target: dict[str, object] | None = None
        remaining: list[dict[str, object]] = []
        for record in pending:
            if str(record.get("approval_id")) == approval_id and target is None:
                target = dict(record)
                continue
            remaining.append(record)
        if target is None:
            raise ApprovalStoreError(f"approval_not_found: {approval_id}")
        target["status"] = action
        target["action"] = action
        target["resolved_at"] = self._now()
        self._write_jsonl(self.pending_file, remaining)
        self._append_jsonl(self.resolved_file, target)
        return target

    def _ensure_files(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.pending_file, self.resolved_file):
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def _read_jsonl(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        try:
            content = path.read_text(encoding="utf-8-sig")
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

    def _append_jsonl(self, path: Path, record: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_jsonl(self, path: Path, records: list[dict[str, object]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
