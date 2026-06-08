"""Lightweight summarizer agent for archive compaction and long-context summaries."""

from __future__ import annotations

import json
import urllib.request


class SummarizerAgent:
    """Create compact summaries suitable for archive persistence."""

    def __init__(
        self,
        *,
        mode: str = "fake",
        model_provider: str = "fake",
        model_name: str = "fake",
        model_base_url: str | None = None,
        model_api_key: str | None = None,
    ) -> None:
        self.mode = mode
        self.model_provider = model_provider
        self.model_name = model_name
        self.model_base_url = model_base_url
        self.model_api_key = model_api_key

    def summarize(self, history_text: str, memory_text: str = "") -> dict[str, object]:
        """Build a summary and return archive metadata for persistence."""

        if self.mode == "real":
            return {
                "summary": self._summarize_real(history_text=history_text, memory_text=memory_text),
                "archive_mode": "real",
                "archive_model_provider": self.model_provider,
                "archive_model_name": self.model_name,
            }
        return {
            "summary": self._summarize_fake(history_text=history_text, memory_text=memory_text),
            "archive_mode": "fake",
            "archive_model_provider": "fake",
            "archive_model_name": "fake",
        }

    def _summarize_fake(self, *, history_text: str, memory_text: str = "") -> str:
        user_lines = [line.removeprefix("user: ").strip() for line in history_text.splitlines() if line.startswith("user: ")]
        assistant_lines = [
            line.removeprefix("assistant: ").strip() for line in history_text.splitlines() if line.startswith("assistant: ")
        ]
        memory_lines = [line.strip()[2:].strip() for line in memory_text.splitlines() if line.strip().startswith("- ")]
        tool_lines = [line for line in assistant_lines if "tool " in line.lower()]

        def bullets(items: list[str], default: str, limit: int = 3) -> str:
            if not items:
                return f"- {default}"
            return "\n".join(f"- {item}" for item in items[:limit])

        return (
            "## 用户目标\n"
            f"{bullets(user_lines[-3:], '待补充用户目标')}\n\n"
            "## 已完成任务\n"
            f"{bullets(assistant_lines[-3:], '已记录最近回复')}\n\n"
            "## 未完成任务\n"
            "- 待下一轮继续推进当前主题。\n\n"
            "## 关键事实\n"
            f"{bullets(user_lines[-2:] + memory_lines[:2], '暂无关键事实')}\n\n"
            "## 用户偏好\n"
            f"{bullets(memory_lines[:3], '暂无显式偏好')}\n\n"
            "## 工具调用结果\n"
            f"{bullets(tool_lines[:3], '本段历史没有工具结果')}\n\n"
            "## 后续建议\n"
            "- 继续基于该摘要恢复任务前，先确认最新用户目标是否变化。\n"
        )

    def _summarize_real(self, *, history_text: str, memory_text: str) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You compress prior MiniBot conversation history into a concise markdown archive. "
                        "Return only the markdown summary."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Summarize the following history and memory into a compact markdown archive.\n\n"
                        f"HISTORY:\n{history_text}\n\nMEMORY:\n{memory_text}"
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            f"{str(self.model_base_url).rstrip('/')}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.model_api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            response_payload = json.loads(response.read().decode("utf-8"))
        return str(response_payload["choices"][0]["message"].get("content", "")).strip()
