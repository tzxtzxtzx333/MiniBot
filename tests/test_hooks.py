from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.hooks.actions import HookAction, HookActionRegistry, LogAction
from minibot.hooks.hook_manager import HookManager
from minibot.hooks.matchers import ExactMatcher, RegexMatcher


ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(hooks_config: dict[str, object]) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    (temp_root / "configs" / "hooks.json").write_text(
        json.dumps(hooks_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return temp_root


def _prepare_temp_root_with_raw_hooks(raw_text: str) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    (temp_root / "configs" / "hooks.json").write_text(raw_text, encoding="utf-8")
    return temp_root


def test_exact_matcher_matches_exact_value() -> None:
    assert ExactMatcher().matches("hello", "hello") is True
    assert ExactMatcher().matches("hello", "hello world") is False


def test_regex_matcher_matches_pattern() -> None:
    assert RegexMatcher().matches(r"calc|tool", "calculator") is True
    assert RegexMatcher().matches(r"calc|tool", "chat") is False


def test_hook_action_registry_registers_and_creates_actions() -> None:
    class CustomAction(HookAction):
        def execute(self, context: dict[str, object]) -> dict[str, object]:
            result = self._base_result(context)
            result["message"] = "custom"
            return result

    HookActionRegistry.register("custom_test_action", CustomAction)
    action = HookActionRegistry.create(
        "custom_test_action",
        hook_config={"name": "custom", "action": "custom_test_action"},
    )
    result = action.execute({"event": "SessionStart"})
    assert result["message"] == "custom"


def test_hook_manager_exact_and_regex_trigger_results() -> None:
    temp_root = _prepare_temp_root(
        {
            "defaults": {"auto_approve": True},
            "hooks": [
                {
                    "name": "exact_log",
                    "event": "SessionStart",
                    "match_type": "exact",
                    "pattern": "SessionStart",
                    "action": "log",
                    "message": "start",
                },
                {
                    "name": "regex_tag",
                    "event": "PreToolUse",
                    "match_type": "regex",
                    "pattern": "calc.*",
                    "action": "tag",
                    "tags": ["matched"],
                },
            ],
        }
    )
    try:
        manager = HookManager(temp_root / "configs" / "hooks.json")
        start_results = manager.trigger("SessionStart", "SessionStart", {})
        assert start_results[0]["message"] == "start"

        tool_results = manager.trigger("PreToolUse", "calculator", {})
        assert tool_results[0]["tags"] == ["matched"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_hook_manager_loads_bom_prefixed_hooks_json() -> None:
    temp_root = _prepare_temp_root_with_raw_hooks(
        "\ufeff"
        + json.dumps(
            {
                "defaults": {"auto_approve": True},
                "hooks": [
                    {
                        "name": "session_start_log",
                        "event": "SessionStart",
                        "match_type": "exact",
                        "pattern": "SessionStart",
                        "action": "log",
                        "message": "bom_ok",
                    },
                    {
                        "name": "pretool_regex_log",
                        "event": "PreToolUse",
                        "match_type": "regex",
                        "pattern": "calc.*",
                        "action": "log",
                        "message": "regex_ok",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    try:
        manager = HookManager(temp_root / "configs" / "hooks.json")
        start_results = manager.trigger("SessionStart", "SessionStart", {})
        assert start_results[0]["message"] == "bom_ok"
        tool_results = manager.trigger("PreToolUse", "calculator", {})
        assert tool_results[0]["message"] == "regex_ok"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_invalid_hook_json_returns_clear_error_without_traceback() -> None:
    temp_root = _prepare_temp_root_with_raw_hooks("{bad json")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "minibot", "chat", "--message", "普通聊天"],
            cwd=temp_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            check=False,
        )
        assert completed.returncode == 1
        assert "Invalid hook config:" in completed.stderr
        assert "Traceback" not in completed.stderr
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_hook_block_prevents_tool_execution() -> None:
    temp_root = _prepare_temp_root(
        {
            "defaults": {"auto_approve": True},
            "hooks": [
                {
                    "name": "block_calculator",
                    "event": "PreToolUse",
                    "match_type": "exact",
                    "pattern": "calculator",
                    "action": "block",
                }
            ],
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-block",
                content="请帮我计算 2 + 3",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        assert result.response == "MiniBot tool blocked: calculator blocked_by_hook"
        assert trace["tool_results"][0]["status"] == "blocked"
        assert trace["failure_category"] == "blocked_by_hook"
        assert trace["final_response"] == "MiniBot tool blocked: calculator blocked_by_hook"
        assert any(item["action"] == "block" and item["blocked"] is True for item in trace["hook_results"])
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_hook_redact_updates_final_response_and_records_fields() -> None:
    temp_root = _prepare_temp_root(
        {
            "defaults": {"auto_approve": True},
            "hooks": [
                {
                    "name": "redact_secret",
                    "event": "AfterResponse",
                    "match_type": "regex",
                    "pattern": "secret",
                    "action": "redact",
                    "replacement": "[REDACTED]",
                    "target_field": "value",
                }
            ],
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-redact",
                content="my secret",
            )
        )
        assert result.response == "MiniBot echo: my [REDACTED]"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        redact_result = next(item for item in trace["hook_results"] if item["action"] == "redact")
        assert redact_result["redacted_fields"] == ["value"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_hook_require_approval_respects_auto_approve() -> None:
    temp_root = _prepare_temp_root(
        {
            "defaults": {"auto_approve": False},
            "hooks": [
                {
                    "name": "approve_calculator",
                    "event": "PreToolUse",
                    "match_type": "exact",
                    "pattern": "calculator",
                    "action": "require_approval",
                    "auto_approve": False,
                    "interactive": False,
                }
            ],
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-approval",
                content="请帮我计算 2 + 3",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        approval_result = next(item for item in trace["hook_results"] if item["action"] == "require_approval")
        assert result.response == "MiniBot tool blocked: calculator approval_denied"
        assert approval_result["status"] == "denied"
        assert trace["tool_results"][0]["status"] == "blocked"
        assert trace["failure_category"] == "approval_denied"
        assert trace["final_response"] == "MiniBot tool blocked: calculator approval_denied"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_default_empty_hooks_config_does_not_break_chat_or_tool_call() -> None:
    temp_root = _prepare_temp_root({"hooks": []})
    try:
        app = MiniBotApp(temp_root)
        chat_result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-empty-hooks-chat",
                content="hello",
            )
        )
        tool_result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-empty-hooks-tool",
                content="calculate 2 + 3",
            )
        )
        assert chat_result.response == "MiniBot echo: hello"
        assert tool_result.response == "MiniBot tool result: 5"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_tag_hook_adds_default_tag_without_blocking() -> None:
    temp_root = _prepare_temp_root(
        {
            "hooks": [
                {
                    "name": "tag_calculator_tool",
                    "event": "PreToolUse",
                    "match_type": "exact",
                    "pattern": "calculator",
                    "action": "tag",
                }
            ]
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-default-tag",
                content="calculate 6 * 7",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        tag_result = next(item for item in trace["hook_results"] if item["action"] == "tag")
        assert tag_result["tags"] == ["hook:tag_calculator_tool"]
        assert tag_result["message"] == "tagged"
        assert trace["tool_results"][0]["status"] == "success"
        assert result.response == "MiniBot tool result: 42"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_tag_hook_uses_configured_tags_without_blocking() -> None:
    temp_root = _prepare_temp_root(
        {
            "hooks": [
                {
                    "name": "tag_calculator_tool",
                    "event": "PreToolUse",
                    "match_type": "exact",
                    "pattern": "calculator",
                    "action": "tag",
                    "tags": ["tool:calculator", "risk:low"],
                }
            ]
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-configured-tag",
                content="calculate 6 * 7",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        tag_result = next(item for item in trace["hook_results"] if item["action"] == "tag")
        assert tag_result["tags"] == ["tool:calculator", "risk:low"]
        assert tag_result["message"] == "tagged"
        assert trace["tool_results"][0]["status"] == "success"
        assert result.response == "MiniBot tool result: 42"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_hook_exception_is_recorded_without_crashing_main_flow() -> None:
    class ExplodingAction(HookAction):
        def execute(self, context: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("boom")

    HookActionRegistry.register("explode_test_action", ExplodingAction)
    temp_root = _prepare_temp_root(
        {
            "defaults": {"auto_approve": True},
            "hooks": [
                {
                    "name": "explode",
                    "event": "UserMessageReceived",
                    "match_type": "exact",
                    "pattern": "普通聊天",
                    "action": "explode_test_action",
                }
            ],
        }
    )
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="session-explode",
                content="普通聊天",
            )
        )
        assert result.response == "MiniBot echo: 普通聊天"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        trace = json.loads(run_path.read_text(encoding="utf-8"))
        error_result = next(item for item in trace["hook_results"] if item["hook_name"] == "explode")
        assert error_result["status"] == "error"
        assert error_result["error"] == "boom"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
