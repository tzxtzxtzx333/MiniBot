"""Runtime workspace management for `.minibot/`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WorkspacePaths:
    """Common workspace file paths."""

    root: Path
    memory_file: Path
    history_file: Path
    archives_dir: Path
    sessions_dir: Path
    runs_dir: Path
    sandbox_dir: Path
    approvals_dir: Path
    approvals_pending_file: Path
    approvals_resolved_file: Path
    evidence_dir: Path


class WorkspaceManager:
    """Create and maintain the MiniBot runtime workspace."""

    def __init__(self, project_root: Path, workspace_dir: str) -> None:
        self.project_root = project_root
        self.root = project_root / workspace_dir
        self.memory_file = self.root / "MEMORY.md"
        self.history_file = self.root / "HISTORY.md"
        self.archives_dir = self.root / "archives"
        self.sessions_dir = self.root / "sessions"
        self.runs_dir = self.root / "runs"
        self.sandbox_dir = self.root / "sandbox_workspace"
        self.approvals_dir = self.root / "approvals"
        self.approvals_pending_file = self.approvals_dir / "pending.jsonl"
        self.approvals_resolved_file = self.approvals_dir / "resolved.jsonl"
        self.logs_dir = self.root / "logs"
        self.tasks_dir = self.root / "tasks"
        self.evidence_dir = self.root / "evidence"
        self.plans_dir = self.root / "plans"

    @property
    def paths(self) -> WorkspacePaths:
        """Return a snapshot of workspace paths."""

        return WorkspacePaths(
            root=self.root,
            memory_file=self.memory_file,
            history_file=self.history_file,
            archives_dir=self.archives_dir,
            sessions_dir=self.sessions_dir,
            runs_dir=self.runs_dir,
            sandbox_dir=self.sandbox_dir,
            approvals_dir=self.approvals_dir,
            approvals_pending_file=self.approvals_pending_file,
            approvals_resolved_file=self.approvals_resolved_file,
            evidence_dir=self.evidence_dir,
        )

    def ensure(self) -> WorkspacePaths:
        """Ensure the workspace and required files exist."""

        for path in [
            self.root,
            self.archives_dir,
            self.sessions_dir,
            self.runs_dir,
            self.sandbox_dir,
            self.approvals_dir,
            self.logs_dir,
            self.tasks_dir,
            self.evidence_dir,
            self.plans_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        for path in [self.approvals_pending_file, self.approvals_resolved_file]:
            if not path.exists():
                path.write_text("", encoding="utf-8")

        if not self.memory_file.exists():
            self.memory_file.write_text("# MEMORY\n\n", encoding="utf-8")
        if not self.history_file.exists():
            self.history_file.write_text("# HISTORY\n\n", encoding="utf-8")
        self._normalize_text_file(self.memory_file, "# MEMORY\n\n")
        self._normalize_text_file(self.history_file, "# HISTORY\n\n")
        return self.paths

    def append_history(self, text: str) -> None:
        """Append a line to the recent history file."""

        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")

    def append_memory(self, text: str) -> None:
        """Append one long-term memory entry."""

        with self.memory_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")

    def append_session(self, session_id: str, text: str) -> None:
        """Append one line to the session-specific transcript file."""

        session_path = self.sessions_dir / f"{session_id}.md"
        if not session_path.exists():
            session_path.write_text(f"# SESSION {session_id}\n\n", encoding="utf-8")
        with session_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")

    def reset_history(self) -> None:
        """Reset recent history after compaction."""

        self.history_file.write_text("# HISTORY\n\n", encoding="utf-8")

    def truncate_history(self, keep_recent_turns: int) -> int:
        """Keep only the most recent N turns in HISTORY.md.

        Returns the number of turns removed.
        """
        if keep_recent_turns <= 0:
            return 0
        content = self.read_history()
        turns = self._parse_turns(content)
        if len(turns) <= keep_recent_turns:
            return 0
        removed = len(turns) - keep_recent_turns
        recent = turns[-keep_recent_turns:]
        new_content = "# HISTORY\n\n"
        for user, assistant in recent:
            if user:
                new_content += f"user: {user}\n"
            if assistant:
                new_content += f"assistant: {assistant}\n"
        self.history_file.write_text(new_content, encoding="utf-8")
        return removed

    @staticmethod
    def _parse_turns(history_text: str) -> list[tuple[str, str]]:
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
                pending_user += " " + line
        if pending_user is not None:
            turns.append((pending_user, ""))
        return turns

    def read_memory(self) -> str:
        """Read `MEMORY.md` as normalized UTF-8 text."""

        return self._read_text_file(self.memory_file, "# MEMORY\n\n")

    def read_history(self) -> str:
        """Read `HISTORY.md` as normalized UTF-8 text."""

        return self._read_text_file(self.history_file, "# HISTORY\n\n")

    def _read_text_file(self, path: Path, default_text: str) -> str:
        """Read a workspace text file with fallback decoding and rewrite UTF-8 when needed."""

        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            normalized = self._decode_with_fallbacks(path.read_bytes(), default_text)
            path.write_text(normalized, encoding="utf-8")
            return normalized

    def _normalize_text_file(self, path: Path, default_text: str) -> None:
        """Ensure the file becomes UTF-8 readable even if a prior run wrote legacy bytes."""

        if not path.exists():
            path.write_text(default_text, encoding="utf-8")
            return
        self._read_text_file(path, default_text)

    @staticmethod
    def _decode_with_fallbacks(raw: bytes, default_text: str) -> str:
        """Best-effort decode for legacy or partially corrupted workspace content."""

        if not raw:
            return default_text
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
