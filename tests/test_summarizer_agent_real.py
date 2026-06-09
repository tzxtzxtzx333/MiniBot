from __future__ import annotations

import json

import pytest

from minibot.subagents.summarizer_agent import SummarizerAgent


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fake_summarizer_returns_fake_archive_metadata() -> None:
    agent = SummarizerAgent(mode="fake")

    result = agent.summarize(history_text="user: hello\nassistant: hi\n", memory_text="- 喜欢中文")

    assert result["archive_mode"] == "fake"
    assert result["archive_model_provider"] == "fake"
    assert result["archive_model_name"] == "fake"
    assert "summary" in result


def test_real_summarizer_uses_model_client_and_returns_real_archive_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "deepseek-chat"
        return _FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "## 用户目标\n- 用真实模型压缩历史\n\n## 后续建议\n- 继续当前任务"
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    agent = SummarizerAgent(
        mode="real",
        model_provider="deepseek",
        model_name="deepseek-chat",
        model_base_url="https://api.deepseek.com",
        model_api_key="test-key",
    )

    result = agent.summarize(history_text="user: hello\nassistant: hi\n", memory_text="- 喜欢中文")

    assert result["archive_mode"] == "real"
    assert result["archive_model_provider"] == "deepseek"
    assert result["archive_model_name"] == "deepseek-chat"
    assert "真实模型压缩历史" in result["summary"]
