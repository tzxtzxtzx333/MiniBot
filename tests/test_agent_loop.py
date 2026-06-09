from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.harness.model_client import ModelFinalAnswer, ModelPlan, OpenAICompatibleModelClient, ToolCall, load_model_client
from minibot.json_utils import load_json_file


ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root() -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    return temp_root


def _write_policy(temp_root: Path, updates: dict[str, object]) -> None:
    policy_path = temp_root / "configs" / "policy.json"
    policy = dict(load_json_file(policy_path))
    policy.update(updates)
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")


def test_agent_loop_persists_response_and_history() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-1",
                content="hello",
            )
        )
        assert result.response == "MiniBot echo: hello"
        history = app.runtime.workspace.history_file.read_text(encoding="utf-8")
        assert "user: hello" in history
        assert "assistant: MiniBot echo: hello" in history
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_run_record_uses_phase1_harness_schema() -> None:
    app = MiniBotApp()
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="session-schema",
            content="schema check",
        )
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = __import__("json").loads(run_path.read_text(encoding="utf-8"))

    assert record["run_id"] == result.run_id
    assert record["channel"] == "test"
    assert record["session_id"] == "session-schema"
    assert record["user_id"] == "tester"
    assert record["user_input"] == "schema check"
    assert record["final_response"] == "MiniBot echo: schema check"
    assert record["context_summary"]
    assert record["tool_calls"] == []
    assert record["tool_results"] == []
    assert record["tool_trace"] == []
    assert isinstance(record["hook_results"], list)
    assert record["model_plan"] == {
        "mode": "chat",
        "reason": "no_tool_call_detected",
        "tool_calls": [],
    }
    assert record["failure_category"] is None
    assert record["retry_count"] == 0
    assert record["partial_success"] is False
    assert record["downgrade_reason"] is None
    assert "started_at" in record
    assert "ended_at" in record


def test_agent_loop_executes_fake_model_tool_call_and_records_lifecycle() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-tool",
                content="请帮我计算 2 + 3",
            )
        )
        assert result.response == "MiniBot tool result: 5"
        assert result.tool_trace

        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["model_plan"] == {
            "mode": "tool_call",
            "reason": "calculation_expression_detected",
            "tool_calls": [
                {
                    "tool_name": "calculator",
                    "arguments": {"expression": "2 + 3"},
                }
            ],
        }
        assert record["tool_calls"] == [
            {
                "tool_name": "calculator",
                "arguments": {"expression": "2 + 3"},
            }
        ]
        assert record["tool_results"][0]["status"] == "success"
        assert record["tool_results"][0]["output"]["result"] == 5
        assert record["failure_category"] is None
        assert isinstance(record["hook_results"], list)
        assert record["final_response"] == "MiniBot tool result: 5"
        assert record["lifecycle_events"] == [
            "SessionStart",
            "UserMessageReceived",
            "MemoryRecall",
            "ContextBuild",
            "PlaceholderClean",
            "ModelPlanning",
            "ToolCallDetected",
            "PreToolUse",
            "ToolGovernanceCheck",
            "ToolExecution",
            "PostToolUse",
            "ToolResultAppend",
            "VerifierCheck",
            "FinalAnswerSynthesis",
            "FinalResponseGenerate",
            "HistoryPersist",
            "RunReportPersist",
            "SessionEnd",
        ]
        history = app.runtime.workspace.history_file.read_text(encoding="utf-8")
        assert "assistant: MiniBot tool result: 5" in history
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_chat_records_chat_model_plan_without_tool_events() -> None:
    app = MiniBotApp(ROOT)
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="session-chat-plan",
            content="普通聊天",
        )
    )
    assert result.response == "MiniBot echo: 普通聊天"

    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert record["model_plan"] == {
        "mode": "chat",
        "reason": "no_tool_call_detected",
        "tool_calls": [],
    }
    assert "PreToolUse" not in record["lifecycle_events"]
    assert "ToolExecution" not in record["lifecycle_events"]
    assert "PostToolUse" not in record["lifecycle_events"]
    assert "ToolResultAppend" not in record["lifecycle_events"]


def test_agent_loop_tool_failure_returns_explicit_failure_response_without_crash() -> None:
    app = MiniBotApp(ROOT)
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="session-tool-failure",
            content="请帮我计算 2 + ",
        )
    )
    assert result.response == "MiniBot tool failed: calculator failed with invalid syntax (<string>, line 1)"

    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert record["model_plan"] == {
        "mode": "tool_call",
        "reason": "calculation_expression_detected",
        "tool_calls": [
            {
                "tool_name": "calculator",
                "arguments": {"expression": "2 +"},
            }
        ],
    }
    assert record["tool_results"][0]["status"] == "failed"
    assert "invalid syntax" in record["tool_results"][0]["error"]
    assert record["failure_category"] == "tool_execution_failed"
    assert record["final_response"] == "MiniBot tool failed: calculator failed with invalid syntax (<string>, line 1)"
    for event_name in ("ToolCallDetected", "PreToolUse", "ToolGovernanceCheck", "ToolExecution", "PostToolUse", "ToolResultAppend"):
        assert event_name in record["lifecycle_events"]


def test_load_model_client_supports_openai_compatible_env_file() -> None:
    temp_root = _prepare_temp_root()
    try:
        config_path = temp_root / "configs" / "minibot.json"
        config = load_json_file(config_path)
        config["model_mode"] = "openai-compatible"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        (temp_root / ".env").write_text(
            "MINIBOT_BASE_URL=https://example.com/v1\n"
            "MINIBOT_API_KEY=test-key\n"
            "MINIBOT_MODEL_NAME=test-model\n",
            encoding="utf-8",
        )

        client = load_model_client(project_root=temp_root, mode="openai-compatible")
        assert isinstance(client, OpenAICompatibleModelClient)
        assert client.base_url == "https://example.com/v1"
        assert client.api_key == "test-key"
        assert client.model == "test-model"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


class _StubRealModelClient:
    def __init__(self, plan: ModelPlan) -> None:
        self._plan = plan
        self.finalize_calls: list[dict[str, object]] = []

    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:  # noqa: ARG002
        return self._plan

    def plan_next(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
        round_index: int,
    ) -> ModelPlan:
        return ModelPlan(
            assistant_message=None,
            tool_calls=[],
            raw_plan={
                "mode": "plan_next",
                "reason": "no_more_tools_needed",
                "tool_calls": [],
            },
        )

    def finalize(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
    ) -> ModelFinalAnswer:
        self.finalize_calls.append(
            {
                "message": message.content,
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            }
        )
        return ModelFinalAnswer(
            content=OpenAICompatibleModelClient(
                base_url="https://api.deepseek.com",
                api_key="test-key",
                model="deepseek-chat",
                provider="deepseek",
            )._fallback_final_response(message, tool_results),
            raw_final_output="synthetic-final-answer",
            model_provider="deepseek",
            model_name="deepseek-chat",
            model_error=None,
            final_answer_mode="real",
            final_answer_used_tool_results=True,
        )

    def finalize_response(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        plan: ModelPlan,
        tool_results: list[dict[str, object]],
    ) -> str:
        return OpenAICompatibleModelClient(
            base_url="https://api.deepseek.com",
            api_key="test-key",
            model="deepseek-chat",
            provider="deepseek",
        ).finalize_response(message, context, plan, tool_results)


def test_agent_loop_executes_real_model_calculator_tool_plan_without_keyword_trigger() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.model_client = _StubRealModelClient(
            ModelPlan(
                assistant_message="use calculator",
                tool_calls=[ToolCall("calculator", {"expression": "128 * 64"})],
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "delegated_to_model_client",
                    "raw_model_output": '{"tool_calls":[{"tool_name":"calculator","arguments":{"expression":"128 * 64"}}]}',
                    "tool_plan": {"tool_calls": [{"tool_name": "calculator", "arguments": {"expression": "128 * 64"}}]},
                    "tool_calls": [{"tool_name": "calculator", "arguments": {"expression": "128 * 64"}}],
                    "model_mode": "real",
                    "model_provider": "deepseek",
                    "model_name": "deepseek-chat",
                    "fake_model": False,
                    "model_error": None,
                },
            )
        )

        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-tool-calc", content="just answer this task")
        )

        assert result.response == "MiniBot tool result: 8192"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["model_plan"]["model_mode"] == "real"
        assert record["model_plan"]["fake_model"] is False
        assert record["tool_calls"] == [{"tool_name": "calculator", "arguments": {"expression": "128 * 64"}}]
        assert record["tool_results"][0]["output"]["result"] == 8192
        assert record["tool_trace"][0]["tool_name"] == "calculator"
        assert app.runtime.agent_loop.model_client.finalize_calls
        assert record["final_answer_mode"] == "real"
        assert record["final_answer_model_provider"] == "deepseek"
        assert record["final_answer_model_name"] == "deepseek-chat"
        assert record["final_answer_used_tool_results"] is True
        assert record["raw_final_answer_output"] == "synthetic-final-answer"
        assert "FinalAnswerSynthesis" in record["lifecycle_events"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_executes_real_model_file_write_tool_plan() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"approval": {"auto_approve": True, "tool_defaults": {"file_write": True}}})
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.model_client = _StubRealModelClient(
            ModelPlan(
                assistant_message="write file",
                tool_calls=[ToolCall("file_write", {"path": "notes/real.txt", "content": "real tool plan"})],
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "delegated_to_model_client",
                    "raw_model_output": '{"tool_calls":[{"tool_name":"file_write","arguments":{"path":"notes/real.txt","content":"real tool plan"}}]}',
                    "tool_plan": {
                        "tool_calls": [
                            {"tool_name": "file_write", "arguments": {"path": "notes/real.txt", "content": "real tool plan"}}
                        ]
                    },
                    "tool_calls": [{"tool_name": "file_write", "arguments": {"path": "notes/real.txt", "content": "real tool plan"}}],
                    "model_mode": "real",
                    "model_provider": "deepseek",
                    "model_name": "deepseek-chat",
                    "fake_model": False,
                    "model_error": None,
                },
            )
        )

        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-tool-write", content="this text should not matter")
        )

        assert result.response.startswith("MiniBot tool result:")
        assert (app.runtime.workspace.sandbox_dir / "notes" / "real.txt").read_text(encoding="utf-8") == "real tool plan"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["tool_calls"][0]["tool_name"] == "file_write"
        assert record["tool_results"][0]["status"] == "success"
        assert record["tool_trace"][0]["status"] == "success"
        assert record["final_answer_mode"] == "real"
        assert record["final_answer_used_tool_results"] is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_fake_mode_keeps_existing_tool_result_template() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="fake-tool-template", content="calculate 2 + 3")
        )
        assert result.response == "MiniBot tool result: 5"
        record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert record["final_answer_mode"] == "fake"
        assert record["final_answer_used_tool_results"] is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_real_mode_finalize_does_not_bypass_approval_required() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.model_client = _StubRealModelClient(
            ModelPlan(
                assistant_message="write file",
                tool_calls=[ToolCall("file_write", {"path": "notes/approval.txt", "content": "needs approval"})],
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "delegated_to_model_client",
                    "tool_calls": [{"tool_name": "file_write", "arguments": {"path": "notes/approval.txt", "content": "needs approval"}}],
                    "model_mode": "real",
                    "model_provider": "deepseek",
                    "model_name": "deepseek-chat",
                    "fake_model": False,
                    "model_error": None,
                },
            )
        )

        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-approval", content="please save this")
        )

        assert "approval required" in result.response.lower()
        record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert record["tool_results"][0]["status"] == "approval_required"
        assert record["final_answer_mode"] == "real"
        assert record["final_answer_used_tool_results"] is True
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_records_tool_parse_error_without_crashing() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.model_client = _StubRealModelClient(
            ModelPlan(
                assistant_message="not-json-but-safe",
                tool_calls=[],
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "tool_parse_error",
                    "raw_model_output": "not-json-but-safe",
                    "tool_plan": None,
                    "tool_calls": [],
                    "tool_parse_error": True,
                    "model_mode": "real",
                    "model_provider": "deepseek",
                    "model_name": "deepseek-chat",
                    "fake_model": False,
                    "model_error": "tool_parse_error",
                },
            )
        )

        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-tool-parse", content="anything")
        )

        assert result.response == "not-json-but-safe"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["model_plan"]["reason"] == "tool_parse_error"
        assert record["model_plan"]["tool_parse_error"] is True
        assert record["tool_calls"] == []
        assert record["tool_results"] == []
        assert "ToolExecution" not in record["lifecycle_events"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_handles_invalid_real_tool_call_structure_without_crashing() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        invalid_plan = ModelPlan(
            assistant_message="bad tool plan",
            tool_calls=[],  # type: ignore[assignment]
            raw_plan={
                "mode": "openai_compatible",
                "reason": "delegated_to_model_client",
                "raw_model_output": '{"tool_calls":[{"tool_name":"calculator","arguments":"bad"}]}',
                "tool_plan": {"tool_calls": [{"tool_name": "calculator", "arguments": "bad"}]},
                "tool_calls": [{"tool_name": "calculator", "arguments": "bad"}],
                "model_mode": "real",
                "model_provider": "deepseek",
                "model_name": "deepseek-chat",
                "fake_model": False,
                "model_error": None,
            },
        )
        invalid_plan.tool_calls = [  # type: ignore[assignment]
            {"tool_name": "calculator", "arguments": "bad"}
        ]
        app.runtime.agent_loop.model_client = _StubRealModelClient(invalid_plan)

        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-invalid-tool", content="no keyword")
        )

        assert "MiniBot tool failed:" in result.response
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["tool_calls"][0]["tool_name"] == "calculator"
        assert record["tool_results"][0]["failure_category"] == "invalid_tool_call"
        assert record["tool_trace"][0]["failure_category"] == "invalid_tool_call"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_real_agent_loop_injects_registry_tool_specs_into_model_prompt(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        import os

        os.environ["MINIBOT_MODEL_MODE"] = "real"
        os.environ["MINIBOT_MODEL_PROVIDER"] = "deepseek"
        os.environ["MINIBOT_MODEL_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MINIBOT_MODEL_API_KEY"] = "test-key"
        os.environ["MINIBOT_MODEL_NAME"] = "deepseek-chat"

        captured: dict[str, object] = {}

        class _FakeResponse:
            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": '{"type":"message","content":"ok"}'}}]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

        def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-tools-prompt", content="calculate 128 * 64")
        )

        system_prompt = captured["body"]["messages"][0]["content"]
        assert "Available tools:" in system_prompt
        assert "calculator" in system_prompt
        assert "file_write" in system_prompt
        assert "python_exec" in system_prompt
        assert "shell_exec" in system_prompt
        assert not system_prompt.rstrip().endswith("Available tools:")
    finally:
        for key in (
            "MINIBOT_MODEL_MODE",
            "MINIBOT_MODEL_PROVIDER",
            "MINIBOT_MODEL_BASE_URL",
            "MINIBOT_MODEL_API_KEY",
            "MINIBOT_MODEL_NAME",
        ):
            os.environ.pop(key, None)
        shutil.rmtree(temp_root, ignore_errors=True)
