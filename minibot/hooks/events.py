"""Hook event definitions."""

from __future__ import annotations

from enum import StrEnum


class HookEvent(StrEnum):
    """Supported hook events in the structured hook runtime."""

    SESSION_START = "SessionStart"
    USER_MESSAGE_RECEIVED = "UserMessageReceived"
    MEMORY_RECALL = "MemoryRecall"
    CONTEXT_BUILD = "ContextBuild"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    TOOL_ERROR = "ToolError"
    BEFORE_RESPONSE = "BeforeResponse"
    AFTER_RESPONSE = "AfterResponse"
    SESSION_END = "SessionEnd"
