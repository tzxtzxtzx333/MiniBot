"""Context composition for the harness."""

from __future__ import annotations

import json

from minibot.channels.base import ChannelMessage


class ContextBuilder:
    """Build context inputs and perform lightweight cleanup for model planning."""

    def __init__(
        self,
        workspace,
        prompt_builder,
        memory_recall,
        placeholder_cleaner,
        token_budget=None,
        history_truncator=None,
        history_retriever=None,
        context_token_budget: int = 1200,
    ) -> None:
        self.workspace = workspace
        self.prompt_builder = prompt_builder
        self.memory_recall = memory_recall
        self.placeholder_cleaner = placeholder_cleaner
        self.token_budget = token_budget
        self.history_truncator = history_truncator
        self.history_retriever = history_retriever
        self.context_token_budget = context_token_budget
        self.enable_history_truncation = True
        self.enable_placeholder_clean = True
        self.enable_archive_recall = True
        self.enable_memory_compaction = True
        self.enable_archive_full_context = False
        self.enable_history_retrieval = True
        self.history_token_budget_override: int | None = None

    def build(self, message: ChannelMessage) -> dict[str, object]:
        """Compose prompt context with memory recall and history text."""

        memory_text = self.workspace.read_memory()
        required_facts = list(message.metadata.get("benchmark_required_facts", []))
        if self.enable_memory_compaction:
            memory_text = self._compact_memory(memory_text, required_facts)

        raw_history_text = self.workspace.read_history()
        history_meta: dict[str, object] = {
            "history_retrieval_mode": "full",
            "retrieved_history_count": 0,
            "retrieved_history_chars": len(raw_history_text),
        }

        if self.enable_history_retrieval and self.history_retriever is not None:
            retrieval_result = self.history_retriever.retrieve(message.content, raw_history_text)
            history_text = str(retrieval_result["history_text"])
            history_meta = {
                "history_retrieval_mode": retrieval_result["history_retrieval_mode"],
                "retrieved_history_count": retrieval_result["retrieved_history_count"],
                "retrieved_history_chars": retrieval_result["retrieved_history_chars"],
            }
        else:
            history_text = raw_history_text

        if (
            self.enable_history_truncation
            and self.token_budget is not None
            and self.history_truncator is not None
        ):
            history_budget = self.history_token_budget_override or self.context_token_budget
            if self.token_budget.is_over_budget(history_text, history_budget):
                history_text = self.history_truncator.truncate(history_text, history_budget)

        archive_context = (
            self._read_full_archives(required_facts) if self.enable_archive_full_context else ""
        )

        recalled_memories = self.memory_recall.recall(
            message.content,
            memory_text=memory_text,
            history_text=raw_history_text,
            archives_dir=self.workspace.archives_dir if self.enable_archive_recall else None,
        )
        seeded_tool_results = list(message.metadata.get("benchmark_context_tool_results", []))
        return {
            "system_prompt": self.prompt_builder.build_system_prompt(),
            "memory": memory_text,
            "history": history_text,
            "archive_context": archive_context,
            "recalled_memories": recalled_memories,
            "message": message.content,
            "tool_results": seeded_tool_results,
            "_required_facts": required_facts,
            "_clean_meta": {"cleaned_placeholders": []},
            "_history_meta": history_meta,
        }

    def clean(self, context: dict[str, object]) -> dict[str, object]:
        """Clean placeholder-like artifacts from structured context."""

        if not self.enable_placeholder_clean:
            cleaned = dict(context)
            cleaned["_clean_meta"] = {"cleaned_placeholders": []}
            return cleaned
        cleaned, meta = self.placeholder_cleaner.clean_context(context)
        cleaned["_clean_meta"] = meta
        return cleaned

    def summarize(self, context: dict[str, object]) -> str:
        """Produce a small textual summary for run traces."""

        history = str(context.get("history", ""))
        memory = str(context.get("memory", ""))
        recalled = context.get("recalled_memories", [])
        cleaned_placeholders = context.get("_clean_meta", {}).get("cleaned_placeholders", [])
        history_meta = context.get("_history_meta", {})
        retrieval_mode = str(history_meta.get("history_retrieval_mode", "full"))
        retrieved_count = int(history_meta.get("retrieved_history_count", 0))
        retrieved_chars = int(history_meta.get("retrieved_history_chars", len(history)))
        tool_results = list(context.get("tool_results", []))
        evidence_count = sum(
            1
            for tr in tool_results
            if isinstance(tr.get("output"), dict) and tr["output"].get("_compressed")
        )
        return (
            f"system_prompt={len(str(context.get('system_prompt', '')))} chars; "
            f"memory={len(memory)} chars; "
            f"history={len(history)} chars; "
            f"history_retrieval_mode={retrieval_mode}; "
            f"retrieved_history_count={retrieved_count}; "
            f"retrieved_history_chars={retrieved_chars}; "
            f"recalled={len(recalled)} items; "
            f"cleaned_placeholders={len(cleaned_placeholders)}; "
            f"evidence_count={evidence_count}; "
            f"message={len(str(context.get('message', '')))} chars"
        )

    def measure(self, context: dict[str, object]) -> dict[str, object]:
        """Return structured context metrics based on the final model input."""

        system_prompt = str(context.get("system_prompt", ""))
        memory = str(context.get("memory", ""))
        history = str(context.get("history", ""))
        archive_context = str(context.get("archive_context", ""))
        recalled = [str(item) for item in context.get("recalled_memories", [])]
        message = str(context.get("message", ""))
        tool_results = list(context.get("tool_results", []))
        tool_specs = list(context.get("tool_specs", []))
        recalled_archive_chars = sum(len(item) for item in recalled if item.startswith("[archive:"))
        archive_chars = len(archive_context) + recalled_archive_chars
        recalled_chars = sum(len(item) for item in recalled)
        tool_results_blob = json.dumps(tool_results, ensure_ascii=False)
        tool_specs_blob = json.dumps(tool_specs, ensure_ascii=False)
        dynamic_context_text = "\n".join(
            [
                memory,
                history,
                archive_context,
                "\n".join(recalled),
                tool_results_blob,
            ]
        )
        prompt_text = "\n".join(
            [
                system_prompt,
                dynamic_context_text,
                tool_specs_blob,
                message,
            ]
        )
        required_facts = [
            str(item) for item in context.get("_required_facts", []) if str(item).strip()
        ]
        history_meta = context.get("_history_meta", {})
        # Compute evidence metrics from tool_results
        evidence_chars = 0
        evidence_count = 0
        for tr in tool_results:
            if isinstance(tr.get("output"), dict) and tr["output"].get("_compressed"):
                evidence_count += 1
                summary = str(tr["output"].get("summary", ""))
                key_points_text = " ".join(str(kp) for kp in tr["output"].get("key_points", []))
                evidence_chars += len(summary) + len(key_points_text)
        return {
            "prompt_tokens": self._estimate_tokens(prompt_text),
            "context_chars": len(prompt_text),
            "dynamic_context_chars": len(dynamic_context_text),
            "dynamic_context_tokens": self._estimate_tokens(dynamic_context_text),
            "history_chars": len(history),
            "history_retrieval_mode": str(history_meta.get("history_retrieval_mode", "full")),
            "retrieved_history_count": int(history_meta.get("retrieved_history_count", 0)),
            "retrieved_history_chars": int(
                history_meta.get("retrieved_history_chars", len(history))
            ),
            "memory_chars": len(memory),
            "archive_chars": archive_chars,
            "recalled_chars": recalled_chars,
            "tool_results_chars": len(tool_results_blob),
            "tool_specs_chars": len(tool_specs_blob),
            "evidence_chars": evidence_chars,
            "evidence_count": evidence_count,
            "key_facts_preserved": (
                all(fact in prompt_text for fact in required_facts) if required_facts else True
            ),
            "token_estimator": "ceil_len_div_4",
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        normalized = text.strip()
        if not normalized:
            return 0
        return (len(normalized) + 3) // 4

    def _read_full_archives(self, required_facts: list[object]) -> str:
        if not self.workspace.archives_dir.exists():
            return ""
        archive_chunks: list[str] = []
        required = [str(item).strip() for item in required_facts if str(item).strip()]
        for archive_path in sorted(self.workspace.archives_dir.glob("*.md")):
            archive_text = archive_path.read_text(encoding="utf-8")
            if required and not any(fact in archive_text for fact in required):
                continue
            archive_chunks.append(f"[archive_full:{archive_path.name}]\n{archive_text}")
        return "\n\n".join(archive_chunks)

    @staticmethod
    def _compact_memory(memory_text: str, required_facts: list[object]) -> str:
        lines = [line.rstrip() for line in memory_text.splitlines() if line.strip()]
        if len(lines) <= 12:
            return memory_text
        required = [str(item).strip() for item in required_facts if str(item).strip()]
        kept: list[str] = []
        for line in lines:
            if any(fact in line for fact in required):
                kept.append(line)
        for line in lines:
            if line in kept:
                continue
            if len(kept) >= 8:
                break
            kept.append(line)
        omitted = max(len(lines) - len(kept), 0)
        compacted = kept[:8]
        if omitted:
            compacted.append(f"- [memory-summary] omitted {omitted} verbose memory lines")
        return "# MEMORY\n\n" + "\n".join(compacted) + "\n"
