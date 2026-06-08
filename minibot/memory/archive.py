"""Archive persistence for compacted history summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class ArchiveWriter:
    """Persist structured history summaries into archive files."""

    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir

    def write(
        self,
        *,
        source_session_id: str,
        summary: str,
        archive_mode: str,
        archive_model_provider: str,
        archive_model_name: str,
        token_before: int,
        token_after: int,
        compression_trigger: str,
    ) -> Path:
        created_at = datetime.now(timezone.utc).isoformat()
        file_name = f"{created_at.replace(':', '-').replace('+00:00', 'Z')}-{source_session_id}.md"
        path = self.archive_dir / file_name
        content = (
            "# ARCHIVE\n\n"
            "summary_by: SummarizerAgent\n"
            f"archive_mode: {archive_mode}\n"
            f"archive_model_provider: {archive_model_provider}\n"
            f"archive_model_name: {archive_model_name}\n"
            f"source_session_id: {source_session_id}\n"
            f"created_at: {created_at}\n"
            f"token_before: {token_before}\n"
            f"token_after: {token_after}\n"
            f"compression_trigger: {compression_trigger}\n\n"
            f"{summary.strip()}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path
