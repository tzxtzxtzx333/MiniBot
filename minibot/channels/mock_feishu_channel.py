"""Mock Feishu adapter used in local development."""

from __future__ import annotations

import json
from pathlib import Path

from .base import BaseChannel, ChannelMessage


class MockFeishuChannel(BaseChannel):
    """Replay a mock Feishu event through the shared AgentLoop."""

    channel_name = "feishu_mock"

    def __init__(self, agent_loop, **kwargs) -> None:
        super().__init__(agent_loop, **kwargs)

    def to_channel_message(self, payload: dict[str, object]) -> ChannelMessage:
        """Convert a mock Feishu event payload into `ChannelMessage`."""

        content = json.loads(payload["event"]["message"]["content"])["text"]
        return ChannelMessage(
            channel=self.channel_name,
            user_id=payload["event"]["sender"]["sender_id"]["user_id"],
            session_id=payload["event"]["message"]["chat_id"],
            content=content,
            metadata={"message_id": payload["event"]["message"]["message_id"]},
        )

    def run_event_file_payload(self, payload: dict[str, object]) -> str:
        """Replay an already-loaded Feishu payload — plan or normal chat."""

        message = self.to_channel_message(payload)
        plan_reply = self.dispatch_plan(message)
        if plan_reply is not None:
            return plan_reply
        return self.dispatch_message(message).response

    def build_reply_payload(self, response_text: str, payload: dict[str, object]) -> dict[str, object]:
        """Package a mock response with Feishu-like reply metadata."""

        message = self.to_channel_message(payload)
        return {
            "channel": self.channel_name,
            "delivery_mode": "mock",
            "session_id": message.session_id,
            "user_id": message.user_id,
            "reply_text": response_text,
            "message": {"text": response_text},
        }

    def run_event_file(self, path: Path) -> str:
        """Load a mock event JSON file and return the assistant reply."""

        payload = json.loads(path.read_text(encoding="utf-8"))
        return self.run_event_file_payload(payload)
