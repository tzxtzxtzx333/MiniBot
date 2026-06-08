"""Prompt construction helpers."""

from __future__ import annotations


class PromptBuilder:
    """Build the system prompt used by MiniBot."""

    def build_system_prompt(self) -> str:
        return (
            "You are MiniBot, a local assistant under active development.\n"
            "Use MEMORY for long-term facts, HISTORY for recent dialogue, and archives for compressed past sessions.\n"
            "Prefer concise, accurate responses and preserve user preferences when they were explicitly remembered."
        )
