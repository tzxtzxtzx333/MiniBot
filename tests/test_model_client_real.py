from __future__ import annotations

import json
import shutil
import urllib.error
import uuid
from pathlib import Path

import pytest

from minibot.channels.base import ChannelMessage
from minibot.config import load_config
from minibot.harness.model_client import OpenAICompatibleModelClient, load_model_client

ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV_KEYS = (
    "MINIBOT_MODEL_MODE",
    "MINIBOT_MODEL_PROVIDER",
    "MINIBOT_MODEL_BASE_URL",
    "MINIBOT_MODEL_API_KEY",
    "MINIBOT_MODEL_NAME",
    "MINIBOT_BASE_URL",
    "MINIBOT_API_KEY",
)


@pytest.fixture
def temp_project_root() -> Path:
    root = ROOT / ".tmp_test_roots" / f"model-config-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in MODEL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_dotenv(root: Path, content: str) -> None:
    (root / ".env").write_text(content, encoding="utf-8")


def _write_config(root: Path, mode: str) -> None:
    configs_dir = root / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / "minibot.json").write_text(
        (
            "{\n"
            '  "app_name": "MiniBot",\n'
            '  "version": "0.1.0",\n'
            '  "workspace_dir": ".minibot",\n'
            f'  "model_mode": "{mode}",\n'
            '  "chat_turn_limit": 20,\n'
            '  "context_token_budget": 1200,\n'
            '  "archive_token_budget": 900,\n'
            '  "http": {"host": "127.0.0.1", "port": 8000}\n'
            "}\n"
        ),
        encoding="utf-8",
    )


def test_real_mode_loads_openai_compatible_client_with_new_fields(
    temp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")

    client = load_model_client(project_root=temp_project_root, mode="real")

    assert isinstance(client, OpenAICompatibleModelClient)
    assert client.base_url == "https://api.deepseek.com"
    assert client.api_key == "test-key"
    assert client.model == "deepseek-chat"


def test_real_mode_missing_api_key_raises_deepseek_config_missing(
    temp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")

    with pytest.raises(RuntimeError, match="deepseek_config_missing"):
        load_model_client(project_root=temp_project_root, mode="real")


def test_fake_mode_remains_available_without_real_settings(temp_project_root: Path) -> None:
    client = load_model_client(project_root=temp_project_root, mode="fake")
    assert client.__class__.__name__ == "FakeModelClient"


def test_real_mode_accepts_legacy_env_keys(
    temp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_API_KEY", "legacy-key")
    monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")

    client = load_model_client(project_root=temp_project_root, mode="real")

    assert isinstance(client, OpenAICompatibleModelClient)
    assert client.base_url == "https://api.deepseek.com"
    assert client.api_key == "legacy-key"


def test_real_mode_does_not_fallback_to_fake_model(
    temp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")

    with pytest.raises(RuntimeError, match="deepseek_config_missing"):
        load_model_client(project_root=temp_project_root, mode="real")


def test_config_env_real_mode_overrides_fake_file(
    temp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(temp_project_root, "fake")
    monkeypatch.setenv("MINIBOT_MODEL_MODE", "real")

    config = load_config(temp_project_root / "configs" / "minibot.json")

    assert config.model_mode == "real"


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_real_model_plan_parses_tool_plan_and_sends_tool_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "type": "tool_plan",
                                    "content": "????????",
                                    "tool_calls": [
                                        {
                                            "tool_name": "calculator",
                                            "arguments": {"expression": "2 + 3"},
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-chat",
        provider="deepseek",
    )
    message = ChannelMessage(
        channel="cli", user_id="u1", session_id="s1", content="???? 2 + 3", metadata={}
    )
    context = {
        "system_prompt": "You are MiniBot.",
        "tool_specs": [
            {
                "name": "calculator",
                "description": "Calculate expressions.",
                "input_schema": {"expression": {"type": "string", "required": True}},
            },
            {
                "name": "file_write",
                "description": "Write a file inside the sandbox workspace.",
                "input_schema": {"path": {"type": "string"}, "content": {"type": "string"}},
            },
            {
                "name": "python_exec",
                "description": "Execute Python code in Docker.",
                "input_schema": {"code": {"type": "string"}},
            },
            {
                "name": "shell_exec",
                "description": "Execute shell commands in Docker.",
                "input_schema": {"command": {"type": "string"}},
            },
        ],
    }

    plan = client.plan(message, context)

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["timeout"] == 15
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"][1]["content"] == "???? 2 + 3"
    assert "tool_specs" not in captured["body"]
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["stream"] is False
    assert "valid JSON" in captured["body"]["messages"][0]["content"]
    assert '"type":"tool_plan"' in captured["body"]["messages"][0]["content"]
    assert "Available tools:" in captured["body"]["messages"][0]["content"]
    assert "calculator" in captured["body"]["messages"][0]["content"]
    assert "file_write" in captured["body"]["messages"][0]["content"]
    assert "python_exec" in captured["body"]["messages"][0]["content"]
    assert "shell_exec" in captured["body"]["messages"][0]["content"]
    assert not captured["body"]["messages"][0]["content"].rstrip().endswith("Available tools:")
    assert plan.assistant_message == "????????"
    assert [call.to_trace() for call in plan.tool_calls] == [
        {"tool_name": "calculator", "arguments": {"expression": "2 + 3"}}
    ]
    assert plan.raw_plan["mode"] == "openai_compatible"
    assert plan.raw_plan["model_mode"] == "real"
    assert plan.raw_plan["model_provider"] == "deepseek"
    assert plan.raw_plan["model_name"] == "deepseek-chat"
    assert plan.raw_plan["fake_model"] is False
    assert plan.raw_plan["model_error"] is None


def test_real_model_plan_records_tool_parse_error_for_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeHttpResponse(
            {"choices": [{"message": {"content": "not-json-but-still-a-response"}}]}
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-chat",
        provider="deepseek",
    )
    message = ChannelMessage(
        channel="cli", user_id="u1", session_id="s1", content="请决定是否需要工具", metadata={}
    )

    plan = client.plan(message, {"system_prompt": "You are MiniBot.", "tool_specs": []})

    assert plan.assistant_message == "not-json-but-still-a-response"
    assert plan.tool_calls == []
    assert plan.raw_plan["reason"] == "tool_parse_error"
    assert plan.raw_plan["model_error"] == "tool_parse_error"
    assert plan.raw_plan["fake_model"] is False


def test_real_model_plan_records_http_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeHttpError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__(
                url="https://api.deepseek.com/chat/completions",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=None,
            )

        def read(self) -> bytes:
            return b'{"error":{"message":"bad request"}}'

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        raise _FakeHttpError()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-chat",
        provider="deepseek",
    )
    message = ChannelMessage(
        channel="cli", user_id="u1", session_id="s1", content="calculate 2 + 3", metadata={}
    )

    plan = client.plan(message, {"system_prompt": "You are MiniBot.", "tool_specs": []})

    assert plan.tool_calls == []
    assert plan.raw_plan["reason"] == "model_http_error"
    assert plan.raw_plan["model_error"] == "model_http_error"
    assert plan.raw_plan["status_code"] == 400
    assert "bad request" in plan.raw_plan["response_body"]
    assert plan.assistant_message == "model_http_error status_code=400"


def test_real_model_finalize_sends_tool_results_and_returns_natural_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHttpResponse(
            {"choices": [{"message": {"content": "根据工具结果，答案是 5。"}}]}
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-chat",
        provider="deepseek",
    )
    final_answer = client.finalize(
        message=ChannelMessage(
            channel="cli", user_id="u1", session_id="s1", content="请帮我算 2 + 3", metadata={}
        ),
        context={"history": "user: 请帮我算 2 + 3", "memory": "", "archives": []},
        tool_calls=[{"tool_name": "calculator", "arguments": {"expression": "2 + 3"}}],
        tool_results=[{"tool_name": "calculator", "status": "success", "output": {"result": 5}}],
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["timeout"] == 15
    assert "Do not call tools again." in captured["body"]["messages"][0]["content"]
    assert "tool_results" in captured["body"]["messages"][1]["content"]
    assert final_answer.content == "根据工具结果，答案是 5。"
    assert final_answer.final_answer_mode == "real"
    assert final_answer.model_provider == "deepseek"
    assert final_answer.model_name == "deepseek-chat"
    assert final_answer.final_answer_used_tool_results is True
