from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from minibot.evals.model_verifier import ModelVerifier


ROOT = Path(__file__).resolve().parents[1]


def test_model_verifier_real_mode_missing_config_returns_structured_missing(monkeypatch) -> None:
    monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
    verifier = ModelVerifier.from_project_root(ROOT)
    result = verifier.verify(
        final_response="MiniBot tool result: 5",
        expected_behavior=["final_response_contains:5"],
        run_record={"tool_calls": [], "tool_results": [], "user_input": "calculate 2 + 3"},
    )
    assert result["passed"] is True
    assert result["failure_category"] == "verifier_config_missing"
    assert result["verifier_mode"] == "real"
    assert result["fake_verifier"] is False


def test_model_verifier_real_mode_success_json_is_parsed(monkeypatch) -> None:
    class _FakeResponse:
        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "passed": True,
                                        "verifier_reason": "matched expected assertions",
                                        "failure_category": None,
                                        "confidence": 0.9,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse()

    monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
    monkeypatch.setenv("MINIBOT_VERIFIER_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
    monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    verifier = ModelVerifier.from_project_root(ROOT)
    result = verifier.verify(
        final_response="MiniBot tool result: 5",
        expected_behavior=["final_response_contains:5"],
        run_record={"tool_calls": [], "tool_results": [], "user_input": "calculate 2 + 3"},
    )
    assert result["used_model"] is True
    assert result["passed"] is True
    assert result["reason"] == "matched expected assertions"
    assert result["confidence"] == 0.9
    assert result["verifier_config_source"] == "dedicated"


def test_model_verifier_real_mode_case_failure_json_is_not_system_error(monkeypatch) -> None:
    class _FakeResponse:
        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "passed": False,
                                        "verifier_reason": "missing expected content",
                                        "failure_category": "missing_expected_content",
                                        "confidence": 0.7,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse()

    monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
    monkeypatch.setenv("MINIBOT_VERIFIER_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
    monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    verifier = ModelVerifier.from_project_root(ROOT)
    result = verifier.verify(
        final_response="MiniBot tool result: 5",
        expected_behavior=["final_response_contains:8192"],
        run_record={"tool_calls": [], "tool_results": [], "user_input": "calculate 128 * 64"},
    )
    assert result["used_model"] is True
    assert result["passed"] is False
    assert result["failure_category"] == "missing_expected_content"
    assert result["reason"] == "missing expected content"


def test_model_verifier_real_mode_invalid_json_returns_parse_error(monkeypatch) -> None:
    class _FakeResponse:
        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(
                {"choices": [{"message": {"content": "not-json"}}]},
                ensure_ascii=False,
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse()

    monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
    monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
    monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    verifier = ModelVerifier.from_project_root(ROOT)
    result = verifier.verify(
        final_response="MiniBot tool result: 5",
        expected_behavior=["final_response_contains:5"],
        run_record={"tool_calls": [], "tool_results": [], "user_input": "calculate 2 + 3"},
    )
    assert result["failure_category"] == "verifier_parse_error"
    assert result["raw_model_output"] == "not-json"


def test_model_verifier_real_mode_http_error_returns_structured_failure(monkeypatch) -> None:
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
            return b'{"error":"bad request for verifier-key"}'

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        raise _FakeHttpError()

    monkeypatch.setenv("MINIBOT_VERIFIER_MODE", "real")
    monkeypatch.setenv("MINIBOT_VERIFIER_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_VERIFIER_API_KEY", "verifier-key")
    monkeypatch.setenv("MINIBOT_VERIFIER_MODEL_NAME", "deepseek-chat")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    verifier = ModelVerifier.from_project_root(ROOT)
    result = verifier.verify(
        final_response="MiniBot tool result: 5",
        expected_behavior=["final_response_contains:5"],
        run_record={"tool_calls": [], "tool_results": [], "user_input": "calculate 2 + 3"},
    )
    assert result["failure_category"] == "verifier_http_error"
    assert result["status_code"] == 400
    assert "verifier-key" not in json.dumps(result, ensure_ascii=False)
