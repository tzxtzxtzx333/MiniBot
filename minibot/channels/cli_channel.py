"""CLI channel adapter."""

from __future__ import annotations

from uuid import uuid4

from .base import BaseChannel, ChannelMessage


class CLIChannel(BaseChannel):
    """Send CLI text into the shared AgentLoop."""

    channel_name = "cli"

    def send_once(self, content: str) -> str:
        """Send a single CLI message and return the assistant reply."""

        message = ChannelMessage(
            channel=self.channel_name,
            user_id="local-user",
            session_id=str(uuid4()),
            content=content,
        )
        return self.dispatch_message(message).response
