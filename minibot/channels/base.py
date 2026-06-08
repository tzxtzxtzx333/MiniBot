"""Channel abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ChannelMessage:
    """Normalized message passed from a channel into the harness."""

    channel: str
    user_id: str
    session_id: str
    content: str
    metadata: dict[str, object] = field(default_factory=dict)


class BaseChannel:
    """Base type for all channel adapters."""

    channel_name = "base"

    def __init__(self, agent_loop) -> None:
        self.agent_loop = agent_loop

    def dispatch_message(self, message: ChannelMessage):
        """Forward a normalized message to the shared AgentLoop."""

        return self.agent_loop.handle_message(message)
