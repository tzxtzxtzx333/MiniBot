from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.governance.approval_store import ApprovalStore
from minibot.harness.model_client import BaseModelClient, ModelPlan, ToolCall
from minibot.json_utils import load_json_file
from minibot.tools.base import BaseTool, ToolResult, ToolSpec


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


class SecretEchoTool(BaseTool):
    spec = ToolSpec(
        name="secret_echo",
        description="Return a string with a secret token.",
        input_schema={
            "type": "object",
            "required": ["text"],
            "additionalProperties": False,
            "properties": {"text": {"type": "string"}},
        },
        risk_level="low",
        sandbox_required=False,
        timeout=2,
        max_retries=0,
    )

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            "text": str(payload["text"]),
            "token": "sk-secret-123456",
            "api_key": "demo-key",
        }


class CountingTool(BaseTool):
    spec = ToolSpec(
        name="counting_tool",
        description="Count executions.",
        input_schema={
            "type": "object",
            "required": ["value"],
            "additionalProperties": False,
            "properties": {"value": {"type": "string"}},
        },
        risk_level="low",
        sandbox_required=False,
        timeout=2,
        max_retries=0,
    )

    def __init__(self) -> None:
        self.calls = 0

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {"value": payload["value"], "calls": self.calls}


class FlakyTool(BaseTool):
    spec = ToolSpec(
        name="flaky_tool",
        description="Fail twice, then succeed.",
        input_schema={
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {},
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=2,
        max_retries=2,
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, payload: dict[str, object]) -> ToolResult:
        self.calls += 1
        if self.calls < 3:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error="temporary_network_error",
                failure_category="temporary_network_error",
                metadata={"attempt": self.calls},
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={"attempt": self.calls, "status": "recovered"},
            metadata={},
        )


class DowngradeTool(BaseTool):
    spec = ToolSpec(
        name="downgrade_tool",
        description="Always fail, then provide a downgrade result.",
        input_schema={
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {},
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=2,
        max_retries=1,
    )

    def execute(self, payload: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            output=None,
            error="temporary_network_error",
            failure_category="temporary_network_error",
            metadata={},
        )

    def downgrade(self, payload: dict[str, object], failure_result: ToolResult) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={"summary": "fallback summary"},
            metadata={"downgraded": True},
        )


class MultiToolPlanModel(BaseModelClient):
    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:
        calls = [
            ToolCall("calculator", {"expression": "2 + 3"}),
            ToolCall("shell_exec", {"command": "rm -rf /"}),
        ]
        return ModelPlan(
            assistant_message=None,
            tool_calls=calls,
            raw_plan={"mode": "tool_call", "reason": "partial_success_test", "tool_calls": [call.to_trace() for call in calls]},
        )


def test_blacklisted_shell_command_is_blocked() -> None:
    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "shell_exec", "arguments": {"command": "rm -rf /"}}]
    )
    assert results[0]["status"] == "blocked"
    assert results[0]["failure_category"] == "blocked_by_policy"
    assert "blacklisted_command" in results[0]["error"]
    assert trace[0]["status"] == "blocked"


def test_high_risk_tool_can_require_approval() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["file_write"],
                "approval": {"auto_approve": False, "tool_defaults": {"file_write": True}},
            },
        )
        app = MiniBotApp(temp_root)
        results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "file_write", "arguments": {"path": "notes/approval.txt", "content": "blocked"}}],
            dispatch_context={"user_id": "tester", "session_id": "approval-dispatch"},
        )
        assert results[0]["status"] == "approval_required"
        assert results[0]["failure_category"] == "approval_required"
        assert results[0]["metadata"]["approval_required"] is True
        assert results[0]["metadata"]["approval_status"] == "pending"
        pending_path = app.runtime.workspace.approvals_pending_file
        lines = [json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines
        assert lines[0]["tool_name"] == "file_write"
        assert lines[0]["status"] == "pending"
        assert lines[0]["user_id"] == "tester"
        assert not (temp_root / ".minibot" / "sandbox_workspace" / "notes" / "approval.txt").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approval_store_list_pending_supports_utf8_bom() -> None:
    temp_root = _prepare_temp_root()
    try:
        store = ApprovalStore(temp_root / ".minibot" / "approvals")
        payload = {
            "approval_id": "bom-pending",
            "status": "pending",
            "user_id": "tester",
            "tool_name": "file_write",
            "arguments": {"path": "notes/demo.txt", "content": "hello"},
            "request_signature": "sig-1",
        }
        store.pending_file.write_text("\ufeff" + json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0]["approval_id"] == "bom-pending"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approval_store_counts_supports_utf8_bom_in_resolved_file() -> None:
    temp_root = _prepare_temp_root()
    try:
        store = ApprovalStore(temp_root / ".minibot" / "approvals")
        resolved_lines = [
            {"approval_id": "a1", "status": "approved", "request_signature": "sig-a"},
            {"approval_id": "a2", "status": "rejected", "request_signature": "sig-b"},
        ]
        store.resolved_file.write_text(
            "\ufeff" + "\n".join(json.dumps(line, ensure_ascii=False) for line in resolved_lines) + "\n",
            encoding="utf-8",
        )
        counts = store.counts()
        assert counts["approved_count"] == 1
        assert counts["rejected_count"] == 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approval_store_ignores_malformed_jsonl_lines() -> None:
    temp_root = _prepare_temp_root()
    try:
        store = ApprovalStore(temp_root / ".minibot" / "approvals")
        store.pending_file.write_text(
            "\n".join(
                [
                    '{"approval_id":"ok-1","status":"pending","request_signature":"sig-ok"}',
                    "{bad json",
                    '{"approval_id":"ok-2","status":"pending","request_signature":"sig-ok-2"}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        pending = store.list_pending()
        assert [item["approval_id"] for item in pending] == ["ok-1", "ok-2"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approved_request_can_execute_on_retry() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["file_write"],
                "approval": {"auto_approve": False, "tool_defaults": {"file_write": True}},
            },
        )
        app = MiniBotApp(temp_root)
        message = "write notes/approval.txt content approved payload"
        first = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="s1", content=message)
        )
        first_run = json.loads((app.runtime.workspace.runs_dir / f"{first.run_id}.json").read_text(encoding="utf-8"))
        approval_id = first_run["tool_results"][0]["metadata"]["approval_id"]
        store = ApprovalStore(app.runtime.workspace.approvals_dir)
        store.approve(approval_id)

        second = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="s2", content=message)
        )
        second_run = json.loads((app.runtime.workspace.runs_dir / f"{second.run_id}.json").read_text(encoding="utf-8"))
        assert second_run["tool_results"][0]["status"] == "success"
        assert second_run["tool_results"][0]["metadata"]["approval_status"] == "approved"
        assert second_run["tool_results"][0]["metadata"]["approval_required"] is False
        assert second_run["tool_trace"][0]["metadata"]["approval_status"] == "approved"
        assert second_run["tool_trace"][0]["metadata"]["approval_id"] == approval_id
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_rejected_request_stays_blocked_on_retry() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["file_write"],
                "approval": {"auto_approve": False, "tool_defaults": {"file_write": True}},
            },
        )
        app = MiniBotApp(temp_root)
        message = "write notes/reject.txt content rejected payload"
        first = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="s1", content=message)
        )
        first_run = json.loads((app.runtime.workspace.runs_dir / f"{first.run_id}.json").read_text(encoding="utf-8"))
        approval_id = first_run["tool_results"][0]["metadata"]["approval_id"]
        store = ApprovalStore(app.runtime.workspace.approvals_dir)
        store.reject(approval_id)

        second = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="s2", content=message)
        )
        second_run = json.loads((app.runtime.workspace.runs_dir / f"{second.run_id}.json").read_text(encoding="utf-8"))
        assert second_run["tool_results"][0]["status"] == "failed"
        assert second_run["tool_results"][0]["failure_category"] == "approval_rejected"
        assert second_run["tool_results"][0]["metadata"]["approval_status"] == "rejected"
        assert not (temp_root / ".minibot" / "sandbox_workspace" / "notes" / "reject.txt").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_blacklisted_command_cannot_be_approved_or_queued() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "approval": {"auto_approve": False, "tool_defaults": {"shell_exec": False}},
            },
        )
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="blk1", content="shell_exec rm -rf /")
        )
        run_record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert run_record["tool_results"][0]["failure_category"] == "blocked_by_policy"
        assert run_record["tool_results"][0]["status"] == "blocked"
        assert app.runtime.workspace.approvals_pending_file.read_text(encoding="utf-8").strip() == ""
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_pending_approval_redacts_sensitive_arguments() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["file_write"],
                "approval": {"auto_approve": False, "tool_defaults": {"file_write": True}},
            },
        )
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="redact-approval",
                content="write notes/secret.txt content api_key is secret-123",
            )
        )
        pending_lines = app.runtime.workspace.approvals_pending_file.read_text(encoding="utf-8")
        assert "secret-123" not in pending_lines
        assert "[REDACTED]" in pending_lines
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_memory_write_requires_approval_when_graylisted() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["memory_write"],
                "approval": {"auto_approve": False, "tool_defaults": {"memory_write": True}},
            },
        )
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="mw1", content="remember approval gated memory")
        )
        run_record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert run_record["tool_results"][0]["tool_name"] == "memory_write"
        assert run_record["tool_results"][0]["status"] == "approval_required"
        assert run_record["tool_results"][0]["failure_category"] == "approval_required"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_python_exec_requires_approval_when_graylisted() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["python_exec"],
                "approval": {"auto_approve": False, "tool_defaults": {"python_exec": True}},
            },
        )
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="py1", content="run python code print(1+1)")
        )
        run_record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert run_record["tool_results"][0]["tool_name"] == "python_exec"
        assert run_record["tool_results"][0]["status"] == "approval_required"
        assert run_record["tool_results"][0]["failure_category"] == "approval_required"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_shell_exec_requires_approval_when_graylisted() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "graylist": ["shell_exec"],
                "approval": {"auto_approve": False, "tool_defaults": {"shell_exec": True}},
            },
        )
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="sh1", content="shell_exec echo hello")
        )
        run_record = json.loads((app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8"))
        assert run_record["tool_results"][0]["tool_name"] == "shell_exec"
        assert run_record["tool_results"][0]["status"] == "approval_required"
        assert run_record["tool_results"][0]["failure_category"] == "approval_required"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_sensitive_data_is_redacted_in_tool_results_and_trace() -> None:
    app = MiniBotApp(ROOT)
    app.runtime.tool_dispatcher.registry.register(SecretEchoTool())
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "secret_echo", "arguments": {"text": "token should disappear"}}]
    )
    assert results[0]["status"] == "success"
    assert results[0]["output"]["token"] == "[REDACTED]"
    assert results[0]["output"]["api_key"] == "[REDACTED]"
    assert trace[0]["output"]["token"] == "[REDACTED]"


def test_sensitive_data_redactor_handles_chinese_token_phrase() -> None:
    app = MiniBotApp(ROOT)
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="redaction-cn",
            content="总结这段文本 我的 api_key 是 sk-test-123 token 是 abc123",
        )
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert "[REDACTED]" in result.response
    assert "abc123" not in result.response
    assert "sk-test-123" not in result.response
    assert "abc123" not in json.dumps(record["tool_trace"], ensure_ascii=False)
    assert "sk-test-123" not in json.dumps(record["tool_results"], ensure_ascii=False)
    assert record["tool_trace"][0]["metadata"]["redacted_fields"]


def test_sensitive_data_redactor_handles_english_password_phrase() -> None:
    app = MiniBotApp(ROOT)
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="redaction-en",
            content="summarize this text password is hello123",
        )
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert "[REDACTED]" in result.response
    assert "hello123" not in result.response
    assert "hello123" not in json.dumps(record["tool_trace"], ensure_ascii=False)
    assert "hello123" not in json.dumps(record["tool_results"], ensure_ascii=False)


def test_duplicate_tool_calls_are_deduplicated() -> None:
    app = MiniBotApp(ROOT)
    tool = CountingTool()
    app.runtime.tool_dispatcher.registry.register(tool)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [
            {"tool_name": "counting_tool", "arguments": {"value": "same"}},
            {"tool_name": "counting_tool", "arguments": {"value": "same"}},
        ]
    )
    assert tool.calls == 1
    assert results[0]["status"] == "success"
    assert results[1]["status"] == "success"
    assert results[1]["metadata"]["deduplicated"] is True


def test_docker_unavailable_returns_structured_failure() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {"approval": {"auto_approve": True, "tool_defaults": {"python_exec": True}}},
        )
        app = MiniBotApp(temp_root)
        app.runtime.tool_dispatcher.docker_executor.available = lambda: False
        results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "python_exec", "arguments": {"code": "print(1 + 1)"}}]
        )
        assert results[0]["status"] == "failed"
        assert results[0]["failure_category"] == "docker_unavailable"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_retry_manager_retries_and_recovers() -> None:
    app = MiniBotApp(ROOT)
    app.runtime.tool_dispatcher.registry.register(FlakyTool())
    results, trace = app.runtime.tool_dispatcher.dispatch([{"tool_name": "flaky_tool", "arguments": {}}])
    assert results[0]["status"] == "success"
    assert results[0]["output"]["status"] == "recovered"
    assert trace[0]["metadata"]["retry_count"] == 2
    assert trace[0]["metadata"]["retry_errors"] == ["temporary_network_error", "temporary_network_error"]


def test_retry_exhaustion_can_downgrade() -> None:
    app = MiniBotApp(ROOT)
    app.runtime.tool_dispatcher.registry.register(DowngradeTool())
    results, trace = app.runtime.tool_dispatcher.dispatch([{"tool_name": "downgrade_tool", "arguments": {}}])
    assert results[0]["status"] == "success"
    assert results[0]["metadata"]["downgraded"] is True
    assert trace[0]["metadata"]["downgrade_reason"] == "retry_exhausted"


def test_cli_style_weather_failure_retries_and_downgrades() -> None:
    app = MiniBotApp(ROOT)
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(
            channel="test",
            user_id="tester",
            session_id="retry-cli",
            content="查询一个模拟失败的天气接口，并给我出行建议",
        )
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert record["tool_calls"][0]["tool_name"] == "weather"
    assert record["retry_count"] > 0
    assert record["retry_errors"]
    assert record["downgrade_reason"] == "retry_exhausted"
    assert record["tool_results"][0]["status"] == "success"
    assert record["tool_results"][0]["metadata"]["downgraded"] is True
    assert "MiniBot downgraded tool result:" in result.response
    assert "fallback" in result.response


def test_agent_loop_records_partial_success_for_mixed_tool_results() -> None:
    app = MiniBotApp(ROOT)
    app.runtime.agent_loop.model_client = MultiToolPlanModel()
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(channel="test", user_id="tester", session_id="partial-success", content="run two tools")
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert record["partial_success"] is True
    assert record["tool_calls"] == [
        {"tool_name": "calculator", "arguments": {"expression": "2 + 3"}},
        {"tool_name": "shell_exec", "arguments": {"command": "rm -rf /"}},
    ]
    assert record["tool_results"][0]["status"] == "success"
    assert record["tool_results"][1]["status"] == "blocked"
    assert record["tool_results"][1]["failure_category"] == "blocked_by_policy"
    assert record["tool_trace"][1]["status"] == "blocked"
    assert result.response.startswith("MiniBot partial success:")
    assert "calculator=5" in result.response
    assert "shell_exec=blacklisted_command" in result.response


def test_cli_style_multi_tool_plan_records_partial_success() -> None:
    app = MiniBotApp(ROOT)
    app.runtime.agent_loop.model_client = MultiToolPlanModel()
    result = app.runtime.agent_loop.handle_message(
        ChannelMessage(channel="test", user_id="tester", session_id="partial-cli", content="stub tool plan")
    )
    run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    assert len(record["tool_calls"]) == 2
    assert record["tool_calls"][0]["tool_name"] == "calculator"
    assert record["tool_calls"][1]["tool_name"] == "shell_exec"
    assert record["tool_results"][0]["status"] == "success"
    assert record["tool_results"][1]["status"] == "blocked"
    assert record["tool_results"][1]["failure_category"] == "blocked_by_policy"
    assert record["partial_success"] is True
    assert "calculator=5" in result.response
    assert "shell_exec=blacklisted_command" in result.response




def test_english_python_exec_phrase_triggers_sandbox_failure() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {"approval": {"auto_approve": True, "tool_defaults": {"python_exec": True}}},
        )
        app = MiniBotApp(temp_root)
        app.runtime.tool_dispatcher.docker_executor.available = lambda: False
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="python-exec-en",
                content="run python code print(1+1)",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["tool_calls"][0]["tool_name"] == "python_exec"
        assert record["tool_results"][0]["failure_category"] == "docker_unavailable"
        assert "requires_sandbox" in result.response or "docker_unavailable" in result.response
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ── tool-level blacklist ──────────────────────────────────────────

def test_tool_level_blacklist_blocks_before_execution() -> None:
    """ToolPolicyManager.validate() raises ToolError for blacklisted tools."""
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"blacklist": ["memory_write"]})
        app = MiniBotApp(temp_root)
        results, trace = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "memory_write", "arguments": {"content": "test fact"}}]
        )
        assert results[0]["status"] == "blocked"
        assert results[0]["success"] is False
        assert results[0]["failure_category"] == "blocked_by_policy"
        assert "blacklisted" in results[0]["error"].lower() or "blocked_by_policy" in results[0]["error"]
        assert results[0].get("output") is None or results[0].get("output") == ""
        assert trace[0]["status"] == "blocked"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_tool_level_blacklist_blocked_result_in_tool_trace() -> None:
    """Blocked-by-blacklist is recorded in both tool_results and tool_trace."""
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"blacklist": ["calculator"]})
        app = MiniBotApp(temp_root)
        results, trace = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "calculator", "arguments": {"expression": "2+2"}}]
        )
        assert results[0]["status"] == "blocked"
        assert results[0]["failure_category"] == "blocked_by_policy"
        assert trace[0]["tool_name"] == "calculator"
        assert trace[0]["status"] == "blocked"
        assert trace[0]["failure_category"] == "blocked_by_policy"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_tool_level_blacklist_recorded_in_run_record_via_agent_loop() -> None:
    """AgentLoop preserves blocked_by_policy from tool-level blacklist in run record."""
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"blacklist": ["memory_write"]})
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="tblk1", content="remember blacklisted tool")
        )
        run_record = json.loads(
            (app.runtime.workspace.runs_dir / f"{result.run_id}.json").read_text(encoding="utf-8")
        )
        assert run_record["tool_results"][0]["tool_name"] == "memory_write"
        assert run_record["tool_results"][0]["status"] == "blocked"
        assert run_record["tool_results"][0]["failure_category"] == "blocked_by_policy"
        assert run_record["failure_category"] == "blocked_by_policy"
        assert run_record["tool_trace"][0]["status"] == "blocked"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_shell_blacklist_still_active_when_tool_blacklist_exists() -> None:
    """Both tool-level and shell-command blacklists work independently."""
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"blacklist": ["memory_write"]})
        app = MiniBotApp(temp_root)
        # shell_blacklist still blocks rm -rf
        results, trace = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "shell_exec", "arguments": {"command": "rm -rf /"}}]
        )
        assert results[0]["status"] == "blocked"
        assert results[0]["failure_category"] == "blocked_by_policy"
        assert "blacklisted_command" in results[0]["error"]
        assert trace[0]["status"] == "blocked"

        # tool-level blacklist still blocks memory_write
        results2, trace2 = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "memory_write", "arguments": {"content": "should be blocked"}}]
        )
        assert results2[0]["status"] == "blocked"
        assert results2[0]["failure_category"] == "blocked_by_policy"
        assert trace2[0]["status"] == "blocked"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
