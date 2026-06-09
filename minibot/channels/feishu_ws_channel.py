"""Feishu WebSocket Bot adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from .base import BaseChannel, ChannelMessage


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeishuWebSocketConfig:
    """Configuration required for the Feishu WebSocket Bot adapter."""

    app_id: str
    app_secret: str
    bot_name: str
    ws_enabled: bool


class FeishuWebSocketChannel(BaseChannel):
    """Connect to the Feishu/Lark WebSocket bot channel and bridge events into AgentLoop."""

    channel_name = "feishu_ws"

    def __init__(self, agent_loop, config: FeishuWebSocketConfig, **kwargs) -> None:
        super().__init__(agent_loop, **kwargs)
        self.config = config
        self._sdk_channel: object | None = None

    @classmethod
    def load_config(cls, env: dict[str, str] | None = None) -> FeishuWebSocketConfig:
        """Build config from FEISHU_* and LARK_* environment variables."""

        source = env or os.environ
        return FeishuWebSocketConfig(
            app_id=(source.get("FEISHU_APP_ID", "") or source.get("LARK_APP_ID", "")).strip(),
            app_secret=(source.get("FEISHU_APP_SECRET", "") or source.get("LARK_APP_SECRET", "")).strip(),
            bot_name=(source.get("FEISHU_BOT_NAME", "MiniBot") or "MiniBot").strip(),
            ws_enabled=(source.get("FEISHU_WS_ENABLED", "true") or "true").strip().lower() == "true",
        )

    @classmethod
    def from_env(cls, agent_loop, env: dict[str, str] | None = None, **kwargs):
        """Build the adapter from environment variables."""

        return cls(agent_loop=agent_loop, config=cls.load_config(env), **kwargs)

    def validate_config(self) -> tuple[bool, str | None]:
        """Validate required Feishu configuration."""

        if not self.config.app_id or not self.config.app_secret or not self.config.ws_enabled:
            return False, "feishu_config_missing"
        return True, None

    def connect(self) -> dict[str, object]:
        """Create the SDK channel instance."""

        sdk = self._import_sdk()
        if isinstance(sdk, dict) and sdk.get("status") == "failed":
            return sdk
        channel_class = sdk["channel_class"]
        channel = channel_class(
            app_id=self.config.app_id,
            app_secret=self.config.app_secret,
        )
        sdk["channel"] = channel
        return {
            "status": "ready",
            "channel": self.channel_name,
            "sdk": sdk,
        }

    def parse_event(self, payload: dict[str, object] | object) -> dict[str, object]:
        """Extract a normalized Feishu event body."""

        if not isinstance(payload, dict):
            payload = self._sdk_message_to_payload(payload)
        event = dict(payload.get("event", {}))
        message_payload = dict(event.get("message", {}))
        sender = dict(dict(event.get("sender", {})).get("sender_id", {}))
        content_blob = message_payload.get("content", "{\"text\": \"\"}")
        text = self._extract_text(content_blob)
        chat_id = str(message_payload.get("chat_id", ""))
        return {
            "event_type": dict(payload.get("header", {})).get("event_type", "im.message.receive_v1"),
            "chat_id": chat_id,
            "message_id": str(message_payload.get("message_id", "")),
            "chat_type": str(message_payload.get("chat_type", "")),
            "user_id": str(sender.get("user_id") or sender.get("open_id") or sender.get("union_id") or "feishu-user"),
            "text": self._strip_bot_mention(text),
            "raw_text": text,
            "content": content_blob,
        }

    def to_channel_message(self, payload: dict[str, object] | object) -> ChannelMessage:
        """Convert a Feishu event payload into the shared `ChannelMessage` structure."""

        parsed = self.parse_event(payload)
        session_id = parsed["chat_id"] or parsed["message_id"] or "feishu-session"
        return ChannelMessage(
            channel=self.channel_name,
            user_id=str(parsed["user_id"]),
            session_id=str(session_id),
            content=str(parsed["text"]),
            metadata={
                "message_id": parsed["message_id"],
                "event_type": parsed["event_type"],
                "chat_id": parsed["chat_id"],
                "chat_type": parsed["chat_type"],
                "raw_text": parsed["raw_text"],
            },
        )

    def send_reply(self, response_text: str, source_message: ChannelMessage) -> dict[str, object]:
        """Package a reply in a Feishu send-message envelope."""

        chat_id = str(source_message.metadata.get("chat_id", source_message.session_id))
        return {
            "channel": self.channel_name,
            "bot_name": self.config.bot_name,
            "session_id": source_message.session_id,
            "user_id": source_message.user_id,
            "chat_id": chat_id,
            "reply_text": response_text,
            "message": {"text": response_text},
        }

    async def deliver_reply(self, sdk_channel: object, reply: dict[str, object]) -> dict[str, object]:
        """Send a reply back through the SDK channel."""

        await sdk_channel.send(str(reply["chat_id"]), dict(reply["message"]))
        return {"status": "sent", "chat_id": reply["chat_id"]}

    def handle_event(self, payload: dict[str, object] | object) -> dict[str, object]:
        """Process one Feishu event — plan or normal chat."""

        message = self.to_channel_message(payload)
        plan_reply = self.dispatch_plan(message)
        if plan_reply is not None:
            reply = self.send_reply(plan_reply, message)
            reply["delivery_mode"] = "ws_adapter"
            reply["dispatch_mode"] = "plan"
            return reply
        result = self.dispatch_message(message)
        reply = self.send_reply(result.response, message)
        reply["delivery_mode"] = "ws_adapter"
        return reply

    def run(self) -> dict[str, object]:
        """Validate config, connect the SDK channel, and block until interrupted."""

        self._ensure_logging()
        ok, error = self.validate_config()
        if not ok:
            return {"status": "failed", "error": error, "channel": self.channel_name}

        logger.info(
            "Starting Feishu websocket: app_id_present=%s secret_present=%s bot_name=%s ws_enabled=%s",
            bool(self.config.app_id),
            bool(self.config.app_secret),
            self.config.bot_name,
            self.config.ws_enabled,
        )

        connection = self.connect()
        if connection.get("status") == "failed":
            return connection

        sdk = dict(connection["sdk"])
        channel = sdk["channel"]
        self._sdk_channel = channel
        channel.on("message", self._handle_sdk_message)

        try:
            logger.info("Feishu websocket connected, entering listen loop")
            asyncio.run(channel.connect())
        except KeyboardInterrupt:
            logger.info("Feishu websocket stopped by user")
            return {"status": "stopped", "channel": self.channel_name}
        return {"status": "stopped", "channel": self.channel_name}

    async def _handle_sdk_message(self, *args: object) -> None:
        if not args:
            logger.warning("Feishu message handler called without args")
            return
        message_event = args[-1]
        logger.info(
            "Received Feishu message event: type=%s attrs=%s",
            type(message_event).__name__,
            [name for name in dir(message_event) if not name.startswith("_")][:30],
        )
        parsed = self.parse_event(message_event)
        logger.info(
            "Received Feishu message event: event_type=%s chat_id=%s message_id=%s",
            parsed["event_type"],
            parsed["chat_id"],
            parsed["message_id"],
        )
        logger.info("Extracted Feishu user text: %s", parsed["text"])

        reply = self.handle_event(message_event)
        logger.info("Agent response: %s", reply["reply_text"])
        sdk_channel = self._sdk_channel
        if sdk_channel is None:
            logger.warning("Feishu SDK channel not initialized, skipping reply delivery")
            return
        try:
            delivery = await self.deliver_reply(sdk_channel, reply)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send Feishu reply: %s", exc)
            return
        logger.info("Feishu reply sent successfully: %s", delivery)

    @staticmethod
    def _import_sdk() -> dict[str, object] | dict[str, str]:
        try:
            from lark_oapi.channel import FeishuChannel
        except (ImportError, ModuleNotFoundError):
            return {"status": "failed", "error": "feishu_sdk_not_installed", "channel": "feishu_ws"}
        return {
            "channel_class": FeishuChannel,
        }

    @staticmethod
    def _extract_text(content_blob: object) -> str:
        if isinstance(content_blob, dict):
            payload = content_blob
        else:
            raw = str(content_blob or "").strip() or "{\"text\": \"\"}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return raw
        if isinstance(payload.get("text"), str):
            return str(payload["text"])
        return str(payload)

    def _strip_bot_mention(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return cleaned
        cleaned = re.sub(r"<at\b[^>]*>.*?</at>", "", cleaned, flags=re.IGNORECASE)
        if self.config.bot_name:
            pattern = rf"^@?{re.escape(self.config.bot_name)}[\s:：\u2005]*"
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def _sdk_message_to_payload(message_event: object) -> dict[str, object]:
        def _value(obj: object, *names: str) -> object:
            for name in names:
                if hasattr(obj, name):
                    return getattr(obj, name)
            return ""

        if any(hasattr(message_event, name) for name in ("chat_id", "message_id", "content_text")):
            text = _value(message_event, "content_text", "text")
            return {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {
                        "sender_id": {
                            "user_id": _value(message_event, "user_id", "open_id", "union_id"),
                        }
                    },
                    "message": {
                        "message_id": _value(message_event, "message_id"),
                        "chat_id": _value(message_event, "chat_id"),
                        "chat_type": _value(message_event, "chat_type"),
                        "content": json.dumps({"text": str(text or "")}, ensure_ascii=False),
                    },
                },
            }

        event = getattr(message_event, "event", message_event)
        message = getattr(event, "message", message_event)
        sender = getattr(event, "sender", None)
        sender_id = getattr(sender, "sender_id", sender)

        return {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_id": {
                        "user_id": _value(sender_id, "user_id", "open_id", "union_id"),
                    }
                },
                "message": {
                    "message_id": _value(message, "message_id"),
                    "chat_id": _value(message, "chat_id"),
                    "chat_type": _value(message, "chat_type"),
                    "content": _value(message, "content", "text"),
                },
            },
        }

    @staticmethod
    def _ensure_logging() -> None:
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
