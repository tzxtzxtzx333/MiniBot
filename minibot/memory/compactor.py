"""Conversation compaction and archive coordination."""

from __future__ import annotations

from minibot.context.token_budget import TokenBudget


class MemoryCompactor:
    """Create archive summaries and write them to `.minibot/archives/`."""

    def __init__(
        self, summarizer_agent, archive_writer, token_budget: TokenBudget | None = None
    ) -> None:
        self.summarizer_agent = summarizer_agent
        self.archive_writer = archive_writer
        self.token_budget = token_budget or TokenBudget()

    def compact(
        self,
        *,
        source_session_id: str,
        history_text: str,
        memory_text: str,
        compression_trigger: str,
        history_turn_count_before: int = 0,
        history_turn_count_after: int = 0,
    ) -> dict[str, object]:
        """Compact recent history into an archive file and return trace metadata."""

        token_before = self.token_budget.estimate_text(history_text)
        summary_result = self.summarizer_agent.summarize(
            history_text=history_text, memory_text=memory_text
        )
        summary = str(summary_result["summary"])
        archive_mode = str(summary_result["archive_mode"])
        archive_model_provider = str(summary_result["archive_model_provider"])
        archive_model_name = str(summary_result["archive_model_name"])
        token_after = self.token_budget.estimate_text(summary)
        archive_path = self.archive_writer.write(
            source_session_id=source_session_id,
            summary=summary,
            archive_mode=archive_mode,
            archive_model_provider=archive_model_provider,
            archive_model_name=archive_model_name,
            token_before=token_before,
            token_after=token_after,
            compression_trigger=compression_trigger,
            history_turn_count_before=history_turn_count_before,
            history_turn_count_after=history_turn_count_after,
        )
        return {
            "summary_by": "SummarizerAgent",
            "archive_mode": archive_mode,
            "archive_model_provider": archive_model_provider,
            "archive_model_name": archive_model_name,
            "archive_path": str(archive_path),
            "source_session_id": source_session_id,
            "compression_trigger": compression_trigger,
            "trigger": compression_trigger,
            "token_before": token_before,
            "token_after": token_after,
            "history_turn_count_before": history_turn_count_before,
            "history_turn_count_after": history_turn_count_after,
        }
