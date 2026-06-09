from __future__ import annotations

import asyncio
import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.channels.feishu_ws_channel import FeishuWebSocketChannel
from minibot.channels.http_channel import HttpChannel
from minibot.channels.mock_feishu_channel import MockFeishuChannel
from minibot.evals.benchmark_runner import BenchmarkRunner


ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root() -> Path:
    tmp_path = ROOT / ".tmp_test_roots" / str(uuid4())
    tmp_path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _http_request(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    timeout: int = 5,
) -> tuple[int, dict[str, object]]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def test_http_channel_status_chat_and_benchmark_routes() -> None:
    app = MiniBotApp(ROOT)
    runner = BenchmarkRunner(app.runtime.agent_loop, ROOT)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=runner,
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        status_code, status_payload = _http_request("GET", f"{base_url}/status")
        assert status_code == 200
        assert status_payload["version"] == "0.1.0"
        assert status_payload["workspace_exists"] is True

        chat_code, chat_payload = _http_request(
            "POST",
            f"{base_url}/chat",
            {"user_id": "http-tester", "session_id": "http-session", "message": "hello over http"},
        )
        assert chat_code == 200
        assert chat_payload["response"] == "MiniBot echo: hello over http"
        assert chat_payload["channel"] == "http"

        benchmark_code, benchmark_payload = _http_request(
            "POST",
            f"{base_url}/benchmark/run",
            {"category": "channel"},
            timeout=30,
        )
        assert benchmark_code == 200
        assert benchmark_payload["status"] == "ok"
        assert benchmark_payload["report"]["phase"] == "phase1_skeleton"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_channel_chat_accepts_content_field_and_persists_run_trace() -> None:
    temp_root = _prepare_temp_root()
    app = MiniBotApp(temp_root)
    runner = BenchmarkRunner(app.runtime.agent_loop, temp_root)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=runner,
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        before = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        chat_code, chat_payload = _http_request(
            "POST",
            f"{base_url}/chat",
            {"user_id": "http-user", "session_id": "http-session-001", "content": "HTTP中文测试"},
        )
        assert chat_code == 200
        assert chat_payload["response"] == "MiniBot echo: HTTP中文测试"

        after = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        new_files = after - before
        assert len(new_files) == 1
        run_path = temp_root / ".minibot" / "runs" / next(iter(new_files))
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        assert payload["channel"] == "http"
        assert payload["user_id"] == "http-user"
        assert payload["session_id"] == "http-session-001"
        assert payload["user_input"] == "HTTP中文测试"
        assert payload["final_response"] == "MiniBot echo: HTTP中文测试"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_http_channel_benchmark_route_returns_json_on_runner_exception() -> None:
    temp_root = _prepare_temp_root()
    app = MiniBotApp(temp_root)

    class _ExplodingRunner:
        def run(self, *args, **kwargs):  # noqa: ANN002
            raise RuntimeError("boom")

    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=_ExplodingRunner(),
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        request = urllib.request.Request(
            f"{base_url}/benchmark/run",
            method="POST",
            data=json.dumps({"category": "channel"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
            raise AssertionError("expected HTTP 500")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            payload = json.loads(exc.read().decode("utf-8"))
            assert payload["status"] == "error"
            assert payload["failure_category"] == "benchmark_error"
            assert "boom" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_http_channel_chat_accepts_text_field() -> None:
    app = MiniBotApp(ROOT)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=BenchmarkRunner(app.runtime.agent_loop, ROOT),
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        chat_code, chat_payload = _http_request(
            "POST",
            f"{base_url}/chat",
            {"user_id": "http-user", "session_id": "http-session-002", "text": "text field message"},
        )
        assert chat_code == 200
        assert chat_payload["response"] == "MiniBot echo: text field message"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_channel_chat_rejects_empty_json_without_creating_run() -> None:
    temp_root = _prepare_temp_root()
    app = MiniBotApp(temp_root)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=BenchmarkRunner(app.runtime.agent_loop, temp_root),
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        before = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        request = urllib.request.Request(
            f"{base_url}/chat",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
            raise AssertionError("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8")) == {"error": "missing_message"}

        after = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        assert after == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_http_channel_chat_rejects_invalid_json_without_creating_run() -> None:
    temp_root = _prepare_temp_root()
    app = MiniBotApp(temp_root)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=BenchmarkRunner(app.runtime.agent_loop, temp_root),
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        before = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        request = urllib.request.Request(
            f"{base_url}/chat",
            method="POST",
            data=b"{bad json",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
            raise AssertionError("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8")) == {"error": "invalid_json"}

        after = {path.name for path in (temp_root / ".minibot" / "runs").glob("*.json")}
        assert after == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_http_channel_unsupported_method_returns_405_json() -> None:
    app = MiniBotApp(ROOT)
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=BenchmarkRunner(app.runtime.agent_loop, ROOT),
    )
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        request = urllib.request.Request(f"{base_url}/chat", method="PUT", data=b"{}", headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
            raise AssertionError("expected HTTP 405")
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
            assert json.loads(exc.read().decode("utf-8")) == {"error": "method_not_allowed"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mock_feishu_channel_converts_event_to_channel_message() -> None:
    app = MiniBotApp(ROOT)
    channel = MockFeishuChannel(app.runtime.agent_loop)
    payload = json.loads((ROOT / "examples" / "mock_feishu_event.json").read_text(encoding="utf-8"))

    message = channel.to_channel_message(payload)
    assert isinstance(message, ChannelMessage)
    assert message.channel == "feishu_mock"
    assert message.user_id == "mock-user"
    assert message.session_id == "chat_mock_001"
    assert message.content == "你好，MiniBot"

    response = channel.run_event_file(ROOT / "examples" / "mock_feishu_event.json")
    assert response == "MiniBot echo: 你好，MiniBot"


def test_feishu_ws_channel_reports_missing_config_without_real_fallback() -> None:
    app = MiniBotApp(ROOT)
    payload = json.loads((ROOT / "examples" / "mock_feishu_event.json").read_text(encoding="utf-8"))

    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "",
            "FEISHU_APP_SECRET": "",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_BOT_MODE": "websocket",
            "FEISHU_WS_ENABLED": "true",
        },
    )

    message = channel.to_channel_message(payload)
    assert isinstance(message, ChannelMessage)
    assert message.channel == "feishu_ws"
    assert message.content == "你好，MiniBot"

    status = channel.run()
    assert status["status"] == "failed"
    assert status["error"] == "feishu_config_missing"


def test_feishu_ws_channel_supports_lark_env_aliases() -> None:
    app = MiniBotApp(ROOT)
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "LARK_APP_ID": "lark-app-id",
            "LARK_APP_SECRET": "lark-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )
    ok, error = channel.validate_config()
    assert ok is True
    assert error is None
    assert channel.config.app_id == "lark-app-id"
    assert channel.config.app_secret == "lark-secret"


def test_feishu_ws_channel_parses_message_payload_to_text_and_chat_id() -> None:
    app = MiniBotApp(ROOT)
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"user_id": "u_123"}},
            "message": {
                "message_id": "om_123",
                "chat_id": "oc_456",
                "chat_type": "p2p",
                "content": "{\"text\":\"@MiniBot hello from feishu\"}",
            },
        },
    }
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )
    parsed = channel.parse_event(payload)
    assert parsed["chat_id"] == "oc_456"
    assert parsed["message_id"] == "om_123"
    assert parsed["text"] == "hello from feishu"

    message = channel.to_channel_message(payload)
    assert message.content == "hello from feishu"
    assert message.metadata["chat_id"] == "oc_456"


def test_feishu_ws_channel_send_reply_builds_feishu_request_payload() -> None:
    app = MiniBotApp(ROOT)
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )
    message = ChannelMessage(
        channel="feishu_ws",
        user_id="u_123",
        session_id="oc_456",
        content="hello",
        metadata={"chat_id": "oc_456"},
    )
    reply = channel.send_reply("MiniBot echo: hello", message)
    assert reply["chat_id"] == "oc_456"
    assert reply["message"] == {"text": "MiniBot echo: hello"}


def test_feishu_ws_channel_handles_event_through_agent_loop_when_configured() -> None:
    app = MiniBotApp(ROOT)
    payload = json.loads((ROOT / "examples" / "mock_feishu_event.json").read_text(encoding="utf-8"))
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_BOT_MODE": "websocket",
            "FEISHU_WS_ENABLED": "true",
        },
    )

    reply = channel.handle_event(payload)
    assert reply["reply_text"] == "MiniBot echo: 你好，MiniBot"
    assert reply["channel"] == "feishu_ws"
    assert reply["delivery_mode"] == "ws_adapter"




def test_feishu_ws_channel_parses_high_level_sdk_message_object() -> None:
    app = MiniBotApp(ROOT)

    class _HighLevelMessage:
        chat_id = "oc_sdk_456"
        message_id = "om_sdk_123"
        content_text = "@MiniBot hello from sdk"
        chat_type = "p2p"
        user_id = "u_sdk_123"

    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )
    parsed = channel.parse_event(_HighLevelMessage())
    assert parsed["chat_id"] == "oc_sdk_456"
    assert parsed["message_id"] == "om_sdk_123"
    assert parsed["text"] == "hello from sdk"


def test_feishu_ws_channel_run_registers_message_handler(monkeypatch) -> None:
    app = MiniBotApp(ROOT)
    registrations: list[tuple[str, str]] = []

    class _FakeSdkChannel:
        def on(self, event_name, handler):  # noqa: ANN001
            registrations.append((event_name, handler.__name__))

        async def connect(self) -> None:
            raise KeyboardInterrupt()

    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )

    monkeypatch.setattr(
        FeishuWebSocketChannel,
        "_import_sdk",
        staticmethod(
            lambda: {
                "channel_class": lambda app_id, app_secret: _FakeSdkChannel(),
            }
        ),
    )
    status = channel.run()
    assert registrations == [("message", "_handle_sdk_message")]
    assert status["status"] == "stopped"


def test_feishu_ws_channel_sdk_handler_accepts_single_message_argument() -> None:
    app = MiniBotApp(ROOT)
    deliveries: list[tuple[str, dict[str, object]]] = []

    class _HighLevelMessage:
        chat_id = "oc_sdk_789"
        message_id = "om_sdk_789"
        content_text = "@MiniBot hello from handler"
        chat_type = "p2p"
        user_id = "u_sdk_789"

    class _FakeSdkChannel:
        async def send(self, chat_id: str, payload: dict[str, object]) -> None:
            deliveries.append((chat_id, payload))

    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_WS_ENABLED": "true",
        },
    )
    channel._sdk_channel = _FakeSdkChannel()

    asyncio.run(channel._handle_sdk_message(_HighLevelMessage()))

    assert deliveries == [("oc_sdk_789", {"text": "MiniBot echo: hello from handler"})]

def test_feishu_ws_channel_returns_sdk_missing_when_configured_without_sdk(monkeypatch) -> None:
    app = MiniBotApp(ROOT)
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        env={
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_BOT_NAME": "MiniBot",
            "FEISHU_BOT_MODE": "websocket",
            "FEISHU_WS_ENABLED": "true",
        },
    )

    monkeypatch.setattr(
        FeishuWebSocketChannel,
        "_import_sdk",
        staticmethod(lambda: {"status": "failed", "error": "feishu_sdk_not_installed", "channel": "feishu_ws"}),
    )
    status = channel.run()
    assert status["status"] == "failed"
    assert status["error"] == "feishu_sdk_not_installed"


# ---------------------------------------------------------------------------
# HTTP Approval API tests
# ---------------------------------------------------------------------------


def _build_http_channel_with_approvals(root: Path) -> HttpChannel:
    """Create an HttpChannel wired with the real approval store from the workspace."""
    from minibot.governance.approval_store import ApprovalStore

    app = MiniBotApp(root)
    runner = BenchmarkRunner(app.runtime.agent_loop, root)
    approval_store = ApprovalStore(app.runtime.workspace.approvals_dir)
    return HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=runner,
        approval_store=approval_store,
    )


def test_http_approvals_list_returns_pending() -> None:
    """GET /approvals returns pending items from the shared ApprovalStore."""
    channel = _build_http_channel_with_approvals(ROOT)
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        # Create a pending approval via the store
        pending = channel.approval_store.create_pending(
            session_id="http-test",
            user_id="tester",
            tool_name="python_exec",
            arguments={"code": "print(1+1)"},
            risk_level="gray",
            reason="approval_denied",
        )

        status_code, payload = _http_request("GET", f"{base_url}/approvals")
        assert status_code == 200
        assert "approvals" in payload
        assert len(payload["approvals"]) >= 1
        match = next(
            (a for a in payload["approvals"] if a["approval_id"] == pending["approval_id"]),
            None,
        )
        assert match is not None
        assert match["status"] == "pending"
        assert match["tool_name"] == "python_exec"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_approve_moves_pending_to_approved() -> None:
    """POST /approvals/{id}/approve changes status to approved."""
    channel = _build_http_channel_with_approvals(ROOT)
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        pending = channel.approval_store.create_pending(
            session_id="http-test",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
            risk_level="gray",
            reason="approval_denied",
        )

        status_code, payload = _http_request(
            "POST", f"{base_url}/approvals/{pending['approval_id']}/approve"
        )
        assert status_code == 200
        assert payload["approval_id"] == pending["approval_id"]
        assert payload["status"] == "approved"

        # Verify in store
        pending_list = channel.approval_store.list_pending()
        assert not any(p["approval_id"] == pending["approval_id"] for p in pending_list)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_reject_moves_pending_to_rejected() -> None:
    """POST /approvals/{id}/reject changes status to rejected."""
    channel = _build_http_channel_with_approvals(ROOT)
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        pending = channel.approval_store.create_pending(
            session_id="http-test",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
            risk_level="gray",
            reason="approval_denied",
        )

        status_code, payload = _http_request(
            "POST", f"{base_url}/approvals/{pending['approval_id']}/reject"
        )
        assert status_code == 200
        assert payload["approval_id"] == pending["approval_id"]
        assert payload["status"] == "rejected"

        # Verify in store
        pending_list = channel.approval_store.list_pending()
        assert not any(p["approval_id"] == pending["approval_id"] for p in pending_list)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_approve_does_not_auto_execute() -> None:
    """HTTP approve only changes status, does not auto-execute the tool."""
    channel = _build_http_channel_with_approvals(ROOT)
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        pending = channel.approval_store.create_pending(
            session_id="http-test",
            user_id="tester",
            tool_name="python_exec",
            arguments={"code": "print('should not run')"},
            risk_level="gray",
            reason="approval_denied",
        )

        # Approve via HTTP
        status_code, payload = _http_request(
            "POST", f"{base_url}/approvals/{pending['approval_id']}/approve"
        )
        assert status_code == 200

        # The resolved record should be approved, but no tool was executed
        resolved = channel.approval_store.find_resolution(
            user_id="tester",
            tool_name="python_exec",
            arguments={"code": "print('should not run')"},
        )
        assert resolved is not None
        assert resolved["status"] == "approved"
        # The run record should not be affected — approval just changes status
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_approval_missing_id_returns_error() -> None:
    """POST /approvals/missing-id/approve returns a structured error."""
    channel = _build_http_channel_with_approvals(ROOT)
    server = channel.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    try:
        url = f"{base_url}/approvals/missing-id/approve"
        data = json.dumps({}).encode("utf-8")
        request = urllib.request.Request(url, method="POST", data=data)
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
            assert False, "expected HTTP error"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            body = json.loads(exc.read().decode("utf-8"))
            assert "error" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cli_and_http_approvals_share_same_store() -> None:
    """CLI approvals list and HTTP GET /approvals read from the same store."""
    temp_root = _prepare_temp_root()
    try:
        channel = _build_http_channel_with_approvals(temp_root)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            # Create via CLI-facing store
            pending = channel.approval_store.create_pending(
                session_id="shared-test",
                user_id="tester",
                tool_name="shell_exec",
                arguments={"command": "echo shared"},
                risk_level="gray",
                reason="approval_denied",
            )

            # Read via HTTP
            _, http_payload = _http_request("GET", f"{base_url}/approvals")
            match = next(
                (a for a in http_payload["approvals"] if a["approval_id"] == pending["approval_id"]),
                None,
            )
            assert match is not None
            assert match["status"] == "pending"

            # Approve via HTTP
            _, approve_payload = _http_request(
                "POST", f"{base_url}/approvals/{pending['approval_id']}/approve"
            )
            assert approve_payload["status"] == "approved"

            # The pending we created should no longer appear in the pending list
            pending_after = channel.approval_store.list_pending()
            assert not any(a["approval_id"] == pending["approval_id"] for a in pending_after)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# HTTP Auth — Bearer Token tests
# ---------------------------------------------------------------------------


def _http_request_with_auth(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    timeout: int = 5,
    auth_token: str | None = None,
) -> tuple[int, dict[str, object]]:
    """Like ``_http_request`` but accepts an optional Bearer token."""
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def _http_request_expect_error(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    timeout: int = 5,
    auth_token: str | None = None,
) -> tuple[int, dict[str, object]]:
    """Make a request that is expected to fail with an HTTP error status."""
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        raise AssertionError("expected HTTP error")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def _build_auth_channel(root: Path, auth_token: str | None) -> HttpChannel:
    """Create an HttpChannel with an optional auth token."""
    from minibot.governance.approval_store import ApprovalStore

    app = MiniBotApp(root)
    runner = BenchmarkRunner(app.runtime.agent_loop, root)
    approval_store = ApprovalStore(app.runtime.workspace.approvals_dir)
    return HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=runner,
        approval_store=approval_store,
        auth_token=auth_token,
    )


class TestHttpAuth:
    """Bearer Token authentication for HTTP Approval API endpoints."""

    AUTH_TOKEN = "test-secret-token-abc123"

    def test_no_token_configured_approvals_work_without_auth(self) -> None:
        """When auth_token is None/empty, approval endpoints work without auth header."""
        channel = _build_auth_channel(ROOT, auth_token=None)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            pending = channel.approval_store.create_pending(
                session_id="auth-test",
                user_id="tester",
                tool_name="file_write",
                arguments={"path": "notes/x.txt", "content": "x"},
                risk_level="gray",
                reason="test",
            )
            # GET /approvals — no auth header needed
            status_code, payload = _http_request("GET", f"{base_url}/approvals")
            assert status_code == 200
            assert len(payload["approvals"]) >= 1

            # POST approve — no auth header needed
            status_code, payload = _http_request(
                "POST", f"{base_url}/approvals/{pending['approval_id']}/approve"
            )
            assert status_code == 200
            assert payload["status"] == "approved"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_get_approvals_without_auth_returns_401(self) -> None:
        """With auth_token set, GET /approvals without header → 401."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request_expect_error("GET", f"{base_url}/approvals")
            assert status_code == 401
            assert payload == {"error": "unauthorized"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_approve_without_auth_returns_401(self) -> None:
        """With auth_token set, POST approve without header → 401."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request_expect_error(
                "POST", f"{base_url}/approvals/test-id/approve"
            )
            assert status_code == 401
            assert payload == {"error": "unauthorized"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_approve_wrong_token_returns_403(self) -> None:
        """With auth_token set, POST approve with wrong token → 403."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request_expect_error(
                "POST",
                f"{base_url}/approvals/test-id/approve",
                auth_token="wrong-token",
            )
            assert status_code == 403
            assert payload == {"error": "forbidden"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_approve_correct_token_returns_approved(self) -> None:
        """With auth_token set, POST approve with correct token → 200 approved."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            pending = channel.approval_store.create_pending(
                session_id="auth-test",
                user_id="tester",
                tool_name="file_write",
                arguments={"path": "notes/x.txt", "content": "x"},
                risk_level="gray",
                reason="test",
            )
            status_code, payload = _http_request_with_auth(
                "POST",
                f"{base_url}/approvals/{pending['approval_id']}/approve",
                auth_token=self.AUTH_TOKEN,
            )
            assert status_code == 200
            assert payload["status"] == "approved"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_reject_without_auth_returns_401(self) -> None:
        """With auth_token set, POST reject without header → 401."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request_expect_error(
                "POST", f"{base_url}/approvals/test-id/reject"
            )
            assert status_code == 401
            assert payload == {"error": "unauthorized"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_reject_wrong_token_returns_403(self) -> None:
        """With auth_token set, POST reject with wrong token → 403."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request_expect_error(
                "POST",
                f"{base_url}/approvals/test-id/reject",
                auth_token="wrong-token",
            )
            assert status_code == 403
            assert payload == {"error": "forbidden"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_post_reject_correct_token_returns_rejected(self) -> None:
        """With auth_token set, POST reject with correct token → 200 rejected."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            pending = channel.approval_store.create_pending(
                session_id="auth-test",
                user_id="tester",
                tool_name="file_write",
                arguments={"path": "notes/x.txt", "content": "x"},
                risk_level="gray",
                reason="test",
            )
            status_code, payload = _http_request_with_auth(
                "POST",
                f"{base_url}/approvals/{pending['approval_id']}/reject",
                auth_token=self.AUTH_TOKEN,
            )
            assert status_code == 200
            assert payload["status"] == "rejected"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_get_status_unaffected_by_auth_token(self) -> None:
        """GET /status always works even when auth_token is configured."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            # Without auth header — should still work
            status_code, payload = _http_request("GET", f"{base_url}/status")
            assert status_code == 200
            assert payload["version"] == "0.1.0"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_post_chat_unaffected_by_auth_token(self) -> None:
        """POST /chat still works without auth even when auth_token is configured."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status_code, payload = _http_request(
                "POST",
                f"{base_url}/chat",
                {"user_id": "u1", "message": "hello with auth configured"},
            )
            assert status_code == 200
            assert "MiniBot echo" in str(payload["response"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_set_get_approvals_with_correct_token_works(self) -> None:
        """With auth_token set, GET /approvals with correct token → 200."""
        channel = _build_auth_channel(ROOT, auth_token=self.AUTH_TOKEN)
        server = channel.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            channel.approval_store.create_pending(
                session_id="auth-test",
                user_id="tester",
                tool_name="file_write",
                arguments={"path": "notes/x.txt", "content": "x"},
                risk_level="gray",
                reason="test",
            )
            status_code, payload = _http_request_with_auth(
                "GET",
                f"{base_url}/approvals",
                auth_token=self.AUTH_TOKEN,
            )
            assert status_code == 200
            assert len(payload["approvals"]) >= 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

