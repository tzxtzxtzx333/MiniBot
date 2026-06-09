from __future__ import annotations

import gzip
import json
import shutil
import urllib.error
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.json_utils import load_json_file
from minibot.tools.base import BaseTool, ToolResult, ToolSpec
from minibot.tools.registry import ToolNotFoundError, ToolValidationError


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


def test_tool_registry_registers_all_tools() -> None:
    app = MiniBotApp(ROOT)
    specs = app.runtime.tool_dispatcher.registry.list_tools()
    names = {spec.name for spec in specs}
    assert len(specs) == 13
    assert names == {
        "calculator",
        "file_read",
        "file_write",
        "web_fetch",
        "web_search",
        "weather",
        "map_route",
        "map_poi_search",
        "python_exec",
        "shell_exec",
        "memory_search",
        "memory_write",
        "doc_summarize",
    }


def test_tool_spec_metadata_is_complete() -> None:
    app = MiniBotApp(ROOT)
    spec = app.runtime.tool_dispatcher.registry.get_spec("python_exec")
    assert spec.name == "python_exec"
    assert spec.description
    assert spec.input_schema["type"] == "object"
    assert spec.risk_level == "high"
    assert spec.sandbox_required is True
    assert spec.timeout > 0
    assert spec.max_retries == 0


def test_tool_registry_query_missing_tool_returns_structured_error() -> None:
    app = MiniBotApp(ROOT)
    try:
        app.runtime.tool_dispatcher.registry.get("missing_tool")
    except ToolNotFoundError as exc:
        assert exc.failure_category == "tool_not_found"
    else:
        raise AssertionError("expected ToolNotFoundError")


def test_tool_registry_schema_validation_success_and_failure() -> None:
    app = MiniBotApp(ROOT)
    registry = app.runtime.tool_dispatcher.registry
    registry.validate_input("calculator", {"expression": "1 + 1"})
    try:
        registry.validate_input("calculator", {"expression": 1})
    except ToolValidationError as exc:
        assert exc.failure_category == "schema_validation_failed"
    else:
        raise AssertionError("expected ToolValidationError")


def test_calculator_tool_dispatch_succeeds() -> None:
    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch([{"tool_name": "calculator", "arguments": {"expression": "128 * 64"}}])
    assert results[0]["status"] == "success"
    assert results[0]["output"]["result"] == 8192
    assert trace[0]["status"] == "success"


def test_file_write_relative_path_uses_sandbox_workspace_and_file_read_reads_it() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"approval": {"auto_approve": True, "tool_defaults": {"file_write": True}}})
        app = MiniBotApp(temp_root)
        sandbox_path = app.runtime.workspace.sandbox_dir / "notes" / "result.txt"
        root_path = temp_root / "notes" / "result.txt"
        write_results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "file_write", "arguments": {"path": "notes/result.txt", "content": "128 * 64 = 8192"}}]
        )
        assert write_results[0]["success"] is True
        assert write_results[0]["output"]["path"] == str(sandbox_path)
        assert sandbox_path.exists()
        assert root_path.exists() is False

        read_results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "file_read", "arguments": {"path": "notes/result.txt"}}]
        )
        assert read_results[0]["success"] is True
        assert read_results[0]["output"]["content"] == "128 * 64 = 8192"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_file_read_missing_file_is_structured_failure() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "file_read", "arguments": {"path": "notes/missing.txt"}}]
        )
        assert results[0]["success"] is False
        assert results[0]["failure_category"] == "file_not_found"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_file_write_blocks_path_escape() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        blocked_paths = ["../outside.txt", "..\\outside.txt", "C:\\temp\\outside.txt", "/tmp/outside.txt"]
        for blocked_path in blocked_paths:
            results, _ = app.runtime.tool_dispatcher.dispatch(
                [{"tool_name": "file_write", "arguments": {"path": blocked_path, "content": "blocked"}}]
            )
            assert results[0]["success"] is False
            assert results[0]["failure_category"] == "path_outside_workspace"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fake_model_invalid_file_write_requests_fail_schema_and_create_no_file() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"approval": {"auto_approve": True, "tool_defaults": {"file_write": True}}})
        app = MiniBotApp(temp_root)
        invalid_messages = [
            "写入 内容缺少路径",
            "写入 notes/missing.txt",
            "写入 内容 只有内容没有路径",
        ]
        for content in invalid_messages:
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(channel="test", user_id="tester", session_id="invalid-write", content=content)
            )
            run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
            record = json.loads(run_path.read_text(encoding="utf-8"))
            assert record["tool_calls"][0]["tool_name"] == "file_write"
            assert record["tool_results"][0]["status"] == "failed"
            assert record["tool_results"][0]["failure_category"] == "schema_validation_failed"
            assert "MiniBot tool failed:" in record["final_response"]
            assert not any(app.runtime.workspace.sandbox_dir.rglob("missing.txt"))
            assert not any(app.runtime.workspace.sandbox_dir.rglob("内容缺少路径"))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_memory_write_and_memory_search_tools() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(temp_root, {"approval": {"auto_approve": True, "tool_defaults": {"memory_write": True}}})
        app = MiniBotApp(temp_root)
        write_results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "memory_write", "arguments": {"content": "我喜欢中文回答"}}]
        )
        assert write_results[0]["success"] is True
        search_results, _ = app.runtime.tool_dispatcher.dispatch(
            [{"tool_name": "memory_search", "arguments": {"query": "中文回答"}}]
        )
        assert search_results[0]["success"] is True
        joined = "\n".join(search_results[0]["output"]["results"])
        assert "中文回答" in joined
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_doc_summarize_tool() -> None:
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "doc_summarize", "arguments": {"text": "这是一段很长的文本，用于测试摘要工具是否能够返回结构化摘要。"}}]
    )
    assert results[0]["success"] is True
    assert "Summary:" in results[0]["output"]["summary"]


def test_mock_tools_return_mock_metadata() -> None:
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [
            {"tool_name": "web_search", "arguments": {"query": "MiniBot"}},
            {"tool_name": "weather", "arguments": {"location": "Shanghai"}},
            {"tool_name": "map_route", "arguments": {"origin": "A", "destination": "B"}},
            {"tool_name": "map_poi_search", "arguments": {"query": "厦门大学附近医院", "location": "厦门大学", "keyword": "医院"}},
        ]
    )
    assert all(result["metadata"].get("provider_status") == "mock" for result in results)
    assert all(result["metadata"].get("mock_provider") is True for result in results)


def test_web_search_tavily_missing_key_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setenv("MINIBOT_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_search", "arguments": {"query": "MiniBot Agent Harness"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "tavily_config_missing"
    assert results[0]["metadata"]["provider_status"] == "missing"
    assert results[0]["metadata"]["provider_name"] == "tavily"
    assert results[0]["metadata"]["real_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False


def test_web_search_tavily_success_response_is_parsed(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        status = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        requests.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "MiniBot Tool Calling",
                        "url": "https://example.com/minibot",
                        "content": "MiniBot supports tool calling.",
                        "score": 0.8,
                    }
                ]
            }
        )

    monkeypatch.setenv("MINIBOT_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret-key")
    monkeypatch.setenv("TAVILY_PROJECT", "demo-project")
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "basic")
    monkeypatch.setenv("TAVILY_MAX_RESULTS", "5")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_search", "arguments": {"query": "MiniBot Agent Harness Tool Calling"}}]
    )

    assert results[0]["success"] is True
    assert requests[0]["url"] == "https://api.tavily.com/search"
    headers = {str(key).lower(): value for key, value in requests[0]["headers"].items()}
    assert headers["authorization"] == "Bearer tavily-secret-key"
    assert headers["content-type"] == "application/json"
    assert headers["x-project-id"] == "demo-project"
    assert requests[0]["body"] == {
        "query": "MiniBot Agent Harness Tool Calling",
        "search_depth": "basic",
        "max_results": 5,
    }
    assert results[0]["output"]["results"][0]["title"] == "MiniBot Tool Calling"
    assert results[0]["output"]["results"][0]["snippet"] == "MiniBot supports tool calling."
    assert results[0]["output"]["results"][0]["score"] == 0.8
    assert results[0]["metadata"]["provider_status"] == "real"
    assert results[0]["metadata"]["provider_name"] == "tavily"
    assert results[0]["metadata"]["real_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False
    assert trace[0]["metadata"]["provider_status"] == "real"


def test_web_search_tavily_http_error_is_structured(monkeypatch) -> None:
    class _FakeHttpError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__(
                url="https://api.tavily.com/search",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )

        def read(self) -> bytes:
            return b'{"error":"bad auth for tavily-secret-key"}'

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        raise _FakeHttpError()

    monkeypatch.setenv("MINIBOT_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_search", "arguments": {"query": "MiniBot"}}]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "tavily_http_error"
    assert results[0]["metadata"]["provider_status"] == "failed"
    assert results[0]["metadata"]["status_code"] == 401
    assert "tavily-secret-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)
    assert "tavily-secret-key" not in json.dumps(trace[0], ensure_ascii=False)


def test_web_search_tavily_network_error_is_structured(monkeypatch) -> None:
    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        raise urllib.error.URLError("network down")

    monkeypatch.setenv("MINIBOT_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_search", "arguments": {"query": "MiniBot"}}]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "tavily_network_error"
    assert results[0]["metadata"]["provider_status"] == "failed"


def test_web_fetch_uses_real_http_provider(monkeypatch) -> None:
    class _FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return b"<html><body>MiniBot Example</body></html>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        assert request.full_url == "https://example.com"
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_fetch", "arguments": {"url": "https://example.com"}}]
    )
    assert results[0]["success"] is True
    assert results[0]["output"]["status_code"] == 200
    assert "MiniBot Example" in results[0]["output"]["text_snippet"]
    assert results[0]["metadata"]["provider_status"] == "real"
    assert results[0]["metadata"]["real_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False
    assert trace[0]["metadata"]["provider_status"] == "real"


def test_web_fetch_rejects_non_http_urls() -> None:
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_fetch", "arguments": {"url": "file:///tmp/demo.txt"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "unsupported_url_scheme"


def test_weather_real_provider_missing_key_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.delenv("MINIBOT_WEATHER_API_KEY", raising=False)
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "Beijing"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "weather_config_missing"
    assert results[0]["metadata"]["provider_status"] == "missing"
    assert results[0]["metadata"]["mock_provider"] is False


def test_weather_real_provider_returns_structured_qweather_result(monkeypatch) -> None:
    responses = [
        {
            "code": "200",
            "location": [
                {
                    "id": "101010100",
                    "name": "北京",
                }
            ],
        },
        {
            "code": "200",
            "now": {
                "text": "晴",
                "temp": "30",
                "humidity": "40",
                "windDir": "北风",
                "windScale": "2",
                "obsTime": "2026-06-06T10:00+08:00",
            },
        },
    ]
    requested_urls: list[str] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        requested_urls.append(request.full_url)
        return _FakeResponse(responses.pop(0))

    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.setenv("MINIBOT_WEATHER_API_KEY", "qweather-key")
    monkeypatch.delenv("MINIBOT_WEATHER_API_HOST", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "北京"}}]
    )

    assert len(requested_urls) == 2
    assert "/geo/v2/city/lookup?" in requested_urls[0]
    assert "/v7/weather/now?" in requested_urls[1]
    assert results[0]["success"] is True
    assert results[0]["output"]["city"] == "北京"
    assert results[0]["output"]["weather"] == "晴"
    assert results[0]["output"]["temperature_c"] == 30
    assert results[0]["output"]["humidity"] == 40
    assert results[0]["output"]["wind_direction"] == "北风"
    assert results[0]["output"]["updated_at"] == "2026-06-06T10:00+08:00"
    assert results[0]["metadata"]["provider_status"] == "real"
    assert results[0]["metadata"]["real_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False
    assert trace[0]["metadata"]["provider_status"] == "real"


def test_map_route_mcp_provider_missing_config_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.delenv("MINIBOT_AMAP_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("MINIBOT_AMAP_MCP_API_KEY", raising=False)
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "A", "destination": "B"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "amap_mcp_config_missing"
    assert results[0]["metadata"]["provider_status"] == "missing"
    assert results[0]["metadata"]["mcp_provider"] is True


def test_map_route_mcp_provider_returns_structured_route_result(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        requests.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": body,
            }
        )
        method = body["method"]
        if method == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "maps_route_plan",
                                "description": "plan route",
                            }
                        ]
                    },
                }
            )
        if method == "tools/call":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "summary": "从厦门大学到厦门站，约 9.8 公里，约 24 分钟。",
                            "distance_km": 9.8,
                            "duration_minutes": 24,
                            "steps": ["沿演武路向北", "进入厦禾路后到达厦门站"],
                        }
                    },
                }
            )
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "厦门大学", "destination": "厦门站"}}]
    )

    assert len(requests) == 2
    assert requests[0]["body"]["method"] == "tools/list"
    assert requests[1]["body"]["method"] == "tools/call"
    assert requests[1]["body"]["params"]["name"] == "maps_route_plan"
    assert results[0]["success"] is True
    assert results[0]["output"]["summary"].startswith("从厦门大学到厦门站")
    assert results[0]["output"]["distance_km"] == 9.8
    assert results[0]["output"]["duration_minutes"] == 24
    assert results[0]["metadata"]["provider_status"] == "mcp"
    assert results[0]["metadata"]["mcp_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False
    assert results[0]["metadata"]["mcp_endpoint_host"] == "mcp.amap.com"
    assert results[0]["metadata"]["request_method"] == "POST"
    assert results[0]["metadata"]["mcp_tool_name"] == "maps_route_plan"
    assert results[0]["metadata"]["request_payload_without_key"]["method"] == "tools/call"
    assert "test-amap-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)
    assert trace[0]["metadata"]["provider_status"] == "mcp"


def test_map_route_mcp_provider_returns_structured_error_on_failure(monkeypatch) -> None:
    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "maps_route_plan"}]},
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": -32000, "message": "upstream route failure"},
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "A", "destination": "B"}}]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "amap_mcp_error"
    assert results[0]["metadata"]["provider_status"] == "failed"
    assert results[0]["metadata"]["mcp_provider"] is True
    assert results[0]["metadata"]["request_payload_without_key"]["method"] == "tools/call"
    assert "upstream route failure" in str(results[0]["error"])
    assert "response_body_snippet" in results[0]["metadata"]
    assert "test-amap-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)


def test_map_route_mcp_geocodes_text_then_calls_driving_route(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        if isinstance(request, str):
            raise AssertionError("rest geocode should not be used")
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {"name": "maps_geo", "inputSchema": {"properties": {"address": {}, "city": {}}}},
                            {
                                "name": "maps_direction_driving",
                                "inputSchema": {"properties": {"origin": {}, "destination": {}, "city": {}}},
                            },
                        ]
                    },
                }
            )
        if body["params"]["name"] == "maps_geo":
            address = body["params"]["arguments"]["address"]
            location = "118.092194,24.435484" if address == "厦门大学" else "118.115895,24.467462"
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"structuredContent": {"location": location, "city": "厦门"}},
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "summary": "driving route",
                        "distance_km": 9.8,
                        "duration_minutes": 24,
                    }
                },
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "厦门大学", "destination": "厦门站", "city": "厦门"}}]
    )

    assert results[0]["success"] is True
    assert calls[1]["params"]["name"] == "maps_geo"
    assert calls[2]["params"]["name"] == "maps_geo"
    assert calls[3]["params"]["name"] == "maps_direction_driving"
    assert calls[3]["params"]["arguments"]["origin"] == "118.092194,24.435484"
    assert calls[3]["params"]["arguments"]["destination"] == "118.115895,24.467462"


def test_map_route_mcp_coordinates_call_driving_directly(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "maps_direction_driving",
                                "inputSchema": {"properties": {"origin": {}, "destination": {}}},
                            }
                        ]
                    },
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"structuredContent": {"summary": "driving route"}},
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [
            {
                "tool_name": "map_route",
                "arguments": {
                    "origin": "118.092194,24.435484",
                    "destination": "118.115895,24.467462",
                    "mode": "driving",
                },
            }
        ]
    )

    assert results[0]["success"] is True
    assert len(calls) == 2
    assert calls[1]["params"]["name"] == "maps_direction_driving"


def test_map_route_mcp_geocode_content_text_results_location_parses_successfully(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        if isinstance(request, str):
            raise AssertionError("rest geocode should not be used")
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {"name": "maps_geo", "inputSchema": {"properties": {"address": {}, "city": {}}}},
                            {
                                "name": "maps_direction_driving",
                                "inputSchema": {"properties": {"origin": {}, "destination": {}, "city": {}}},
                            },
                        ]
                    },
                }
            )
        if body["params"]["name"] == "maps_geo":
            address = body["params"]["arguments"]["address"]
            location = "118.092194,24.435484" if address == "厦门大学" else "118.115895,24.467462"
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"results": [{"location": location, "city": "厦门"}]},
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                        "isError": False,
                    },
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "summary": "driving route",
                        "distance_km": 9.8,
                        "duration_minutes": 24,
                    }
                },
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "厦门大学", "destination": "厦门站", "city": "厦门"}}]
    )

    assert results[0]["success"] is True
    assert calls[1]["params"]["name"] == "maps_geo"
    assert calls[2]["params"]["name"] == "maps_geo"
    assert calls[3]["params"]["name"] == "maps_direction_driving"
    assert calls[3]["params"]["arguments"]["origin"] == "118.092194,24.435484"
    assert calls[3]["params"]["arguments"]["destination"] == "118.115895,24.467462"
    assert "test-amap-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)


def test_map_route_mcp_error_code_without_message_returns_structured_failure(monkeypatch) -> None:
    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "maps_direction_driving"}]},
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": -32603},
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_route", "arguments": {"origin": "118.1,24.4", "destination": "118.2,24.5"}}]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "amap_mcp_error"
    assert "code=-32603" in str(results[0]["error"])


def test_map_route_mcp_parses_json_string_route_content() -> None:
    app = MiniBotApp(ROOT)
    raw_result = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "origin": "118.092194,24.435484",
                        "destination": "118.115895,24.467462",
                        "paths": [
                            {
                                "distance": "7300",
                                "duration": "696",
                                "steps": [
                                    {"instruction": "沿演武路向西南行驶199米靠右"},
                                    {"instruction": "沿演武大桥向西北行驶1.1千米向右前方行驶进入匝道"},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }

    output = app.runtime.tool_dispatcher.registry.get("map_route")._normalize_route_output(  # type: ignore[attr-defined]
        raw_result,
        "厦门大学",
        "厦门站",
    )

    assert output["distance_km"] == 7.3
    assert output["duration_minutes"] == 11.6
    assert "演武路" in output["steps_summary"][0]
    assert output["summary"].startswith("从厦门大学到厦门站")
    assert not output["summary"].startswith("{")


def test_map_poi_search_mcp_provider_missing_config_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.delenv("MINIBOT_AMAP_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("MINIBOT_AMAP_MCP_API_KEY", raising=False)
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "map_poi_search", "arguments": {"query": "厦门大学附近医院"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "amap_mcp_config_missing"
    assert results[0]["metadata"]["provider_status"] == "missing"
    assert results[0]["metadata"]["mcp_provider"] is True


def test_map_poi_search_mcp_provider_returns_structured_results(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        requests.append({"url": request.full_url, "headers": dict(request.header_items()), "body": body})
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "maps_geo",
                                "inputSchema": {"properties": {"address": {}, "city": {}}},
                            },
                            {
                                "name": "maps_around_search",
                                "inputSchema": {
                                    "properties": {"location": {}, "keywords": {}, "city": {}, "radius": {}}
                                },
                            },
                        ]
                    },
                }
            )
        if body["method"] == "tools/call" and body["params"]["name"] == "maps_geo":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"structuredContent": {"location": "118.092194,24.435484", "city": "厦门"}},
                }
            )
        if body["method"] == "tools/call" and body["params"]["name"] == "maps_around_search":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "pois": [
                                            {
                                                "name": "厦门大学附属医院",
                                                "address": "厦门市思明区示例路 1 号",
                                                "distance": "1234",
                                                "location": "118.101000,24.441000",
                                                "type": "医院",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                        "isError": False,
                    },
                }
            )
        raise AssertionError(f"unexpected method: {body['method']}")

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [
            {
                "tool_name": "map_poi_search",
                "arguments": {
                    "query": "厦门大学附近医院",
                    "location": "厦门大学",
                    "keyword": "医院",
                    "city": "厦门",
                    "radius": 3000,
                },
            }
        ]
    )

    assert results[0]["success"] is True
    assert requests[0]["body"]["method"] == "tools/list"
    assert requests[1]["body"]["params"]["name"] == "maps_geo"
    assert requests[2]["body"]["params"]["name"] == "maps_around_search"
    assert results[0]["output"]["results"][0]["name"] == "厦门大学附属医院"
    assert results[0]["output"]["results"][0]["distance_m"] == 1234
    assert results[0]["metadata"]["provider_status"] == "mcp"
    assert results[0]["metadata"]["mcp_provider"] is True
    assert results[0]["metadata"]["mock_provider"] is False
    assert results[0]["metadata"]["mcp_tool_name"] == "maps_around_search"
    assert "test-amap-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)
    assert trace[0]["metadata"]["provider_status"] == "mcp"


def test_map_poi_search_mcp_provider_returns_structured_error_on_failure(monkeypatch) -> None:
    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        body = json.loads(request.data.decode("utf-8"))
        if body["method"] == "tools/list":
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "maps_around_search"}]},
                }
            )
        return _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": -32603},
            }
        )

    monkeypatch.setenv("MINIBOT_MAP_PROVIDER", "mcp")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_ENDPOINT", "https://mcp.amap.com/mcp?key=test-amap-key")
    monkeypatch.setenv("MINIBOT_AMAP_MCP_API_KEY", "test-amap-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [
            {
                "tool_name": "map_poi_search",
                "arguments": {
                    "query": "厦门大学附近医院",
                    "location": "118.092194,24.435484",
                    "keyword": "医院",
                },
            }
        ]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "amap_mcp_error"
    assert results[0]["metadata"]["provider_status"] == "failed"
    assert results[0]["metadata"]["mcp_provider"] is True
    assert results[0]["metadata"]["request_payload_without_key"]["method"] == "tools/call"
    assert "test-amap-key" not in json.dumps(results[0]["metadata"], ensure_ascii=False)


def test_weather_real_provider_uses_header_auth_and_https_host(monkeypatch) -> None:
    responses = iter(
        [
            {"code": "200", "location": [{"id": "101010100", "name": "Beijing"}]},
            {
                "code": "200",
                "now": {
                    "text": "Sunny",
                    "temp": "30",
                    "feelsLike": "32",
                    "humidity": "40",
                    "windDir": "North",
                    "windScale": "2",
                    "obsTime": "2026-06-06T10:00+08:00",
                },
            },
        ]
    )
    requested_urls: list[str] = []
    request_headers: list[dict[str, str]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        requested_urls.append(request.full_url)
        request_headers.append({key: value for key, value in request.header_items()})
        return _FakeResponse(next(responses))

    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.setenv("MINIBOT_WEATHER_API_KEY", "qweather-key")
    monkeypatch.setenv("MINIBOT_WEATHER_API_HOST", "devapi.qweather.com/")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "Beijing"}}]
    )

    assert len(requested_urls) == 2
    assert requested_urls[0].startswith("https://devapi.qweather.com/")
    assert requested_urls[1].startswith("https://devapi.qweather.com/")
    assert "/geo/v2/city/lookup?" in requested_urls[0]
    assert "/v7/weather/now?" in requested_urls[1]
    assert "key=" not in requested_urls[0]
    assert "key=" not in requested_urls[1]
    assert request_headers[0]["X-qw-api-key"] == "qweather-key"
    assert request_headers[0]["Accept"] == "application/json"
    assert request_headers[0]["Accept-encoding"] == "identity"
    assert results[0]["success"] is True
    assert results[0]["output"]["location"] == "Beijing"
    assert results[0]["output"]["city"] == "Beijing"
    assert results[0]["output"]["text"] == "Sunny"
    assert results[0]["output"]["temperature_c"] == 30
    assert results[0]["output"]["feels_like"] == 32
    assert results[0]["output"]["humidity"] == 40
    assert results[0]["output"]["wind_dir"] == "North"
    assert results[0]["output"]["wind_scale"] == "2"
    assert results[0]["output"]["obs_time"] == "2026-06-06T10:00+08:00"
    assert results[0]["metadata"]["provider_status"] == "real"
    assert trace[0]["metadata"]["provider_status"] == "real"


def test_weather_real_provider_reports_non_json_response(monkeypatch) -> None:
    class _FakeResponse:
        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return b"<html>upstream error</html>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse()

    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.setenv("MINIBOT_WEATHER_API_KEY", "qweather-key")
    monkeypatch.setenv("MINIBOT_WEATHER_API_HOST", "devapi.qweather.com")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "Beijing"}}]
    )

    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "weather_non_json_response"
    assert "QWeather returned non-JSON response:" in str(results[0]["error"])


def test_weather_real_provider_decodes_gzip_response(monkeypatch) -> None:
    responses = iter(
        [
            {"code": "200", "location": [{"id": "101010100", "name": "Beijing"}]},
            {
                "code": "200",
                "now": {
                    "text": "Sunny",
                    "temp": "30",
                    "feelsLike": "32",
                    "humidity": "40",
                    "windDir": "North",
                    "windScale": "2",
                    "obsTime": "2026-06-06T10:00+08:00",
                },
            },
        ]
    )

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.headers = {"Content-Encoding": "gzip"}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            raw = json.dumps(self._payload, ensure_ascii=False).encode("utf-8")
            return gzip.compress(raw)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse(next(responses))

    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.setenv("MINIBOT_WEATHER_API_KEY", "qweather-key")
    monkeypatch.setenv("MINIBOT_WEATHER_API_HOST", "devapi.qweather.com")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "Beijing"}}]
    )

    assert results[0]["success"] is True
    assert results[0]["output"]["text"] == "Sunny"


def test_weather_real_provider_error_does_not_leak_api_key(monkeypatch) -> None:
    api_key = "secret-qweather-key"

    class _FakeResponse:
        headers = {}

        def read(self, size: int = -1) -> bytes:  # noqa: ARG002
            return b"\x8b\x00binary\xff"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        return _FakeResponse()

    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "real")
    monkeypatch.setenv("MINIBOT_WEATHER_API_KEY", api_key)
    monkeypatch.setenv("MINIBOT_WEATHER_API_HOST", "devapi.qweather.com")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "weather", "arguments": {"location": "Beijing"}}]
    )

    assert results[0]["success"] is False
    assert api_key not in str(results[0]["error"])
    assert api_key not in json.dumps(results[0]["metadata"], ensure_ascii=False)
    assert api_key not in json.dumps(trace[0], ensure_ascii=False)


def test_web_fetch_returns_structured_failure_on_http_error(monkeypatch) -> None:
    class _FakeHttpError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__(
                url="https://example.com",
                code=502,
                msg="Bad Gateway",
                hdrs=None,
                fp=None,
            )

        def read(self) -> bytes:
            return b"upstream failure"

    def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
        raise _FakeHttpError()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    app = MiniBotApp(ROOT)
    results, _ = app.runtime.tool_dispatcher.dispatch(
        [{"tool_name": "web_fetch", "arguments": {"url": "https://example.com"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "web_fetch_http_error"
    assert results[0]["metadata"]["provider_status"] == "failed"


def test_fake_model_triggers_mock_provider_tools() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        messages = [
            ("weather Xiamen", "weather"),
            ("web search MiniBot tool system", "web_search"),
            ("map route Xiamen University to Xiamen North Station", "map_route"),
            ("帮我查一下厦门大学附近有什么医院", "map_poi_search"),
        ]
        for content, tool_name in messages:
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(channel="test", user_id="tester", session_id="mock-tools", content=content)
            )
            run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
            record = json.loads(run_path.read_text(encoding="utf-8"))
            assert record["tool_calls"][0]["tool_name"] == tool_name
            assert record["tool_results"][0]["status"] == "success"
            assert record["tool_results"][0]["metadata"]["mock_provider"] is True
            assert record["tool_trace"][0]["metadata"]["mock_provider"] is True
            assert not record["final_response"].startswith("MiniBot echo:")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fake_model_routes_nearby_poi_queries_to_map_poi_search() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        messages = [
            "帮我查一下厦门大学附近有什么医院",
            "查找厦门大学附近的咖啡店",
        ]
        for content in messages:
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(channel="test", user_id="tester", session_id="poi-tools", content=content)
            )
            run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
            record = json.loads(run_path.read_text(encoding="utf-8"))
            assert record["tool_calls"][0]["tool_name"] == "map_poi_search"
            assert record["tool_results"][0]["metadata"]["provider"] == "map_poi_search"
            assert record["tool_results"][0]["metadata"]["provider_status"] == "mock"
            assert record["tool_results"][0]["metadata"]["mock_provider"] is True
            assert record["tool_calls"][0]["tool_name"] != "web_search"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_python_and_shell_exec_do_not_run_host_commands() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {"approval": {"auto_approve": True, "tool_defaults": {"python_exec": True, "shell_exec": True}}},
        )
        app = MiniBotApp(temp_root)
        app.runtime.tool_dispatcher.docker_executor.available = lambda: False
        results, _ = app.runtime.tool_dispatcher.dispatch(
            [
                {"tool_name": "python_exec", "arguments": {"code": "print('hi')"}},
                {"tool_name": "shell_exec", "arguments": {"command": "echo hi"}},
            ]
        )
        assert results[0]["success"] is False
        assert results[0]["failure_category"] == "docker_unavailable"
        assert results[0]["metadata"]["sandbox"] == "docker"
        assert results[1]["success"] is False
        assert results[1]["failure_category"] == "docker_unavailable"
        assert results[1]["metadata"]["sandbox"] == "docker"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fake_model_triggers_sandbox_required_tools_without_host_execution() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {"approval": {"auto_approve": True, "tool_defaults": {"python_exec": True, "shell_exec": True}}},
        )
        app = MiniBotApp(temp_root)
        app.runtime.tool_dispatcher.docker_executor.available = lambda: False
        messages = [
            ("运行python代码 print(1+1)", "python_exec"),
            ("执行shell命令 echo hello", "shell_exec"),
        ]
        for content, tool_name in messages:
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(channel="test", user_id="tester", session_id="sandbox-tools", content=content)
            )
            run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
            record = json.loads(run_path.read_text(encoding="utf-8"))
            assert record["tool_calls"][0]["tool_name"] == tool_name
            assert record["tool_results"][0]["status"] == "failed"
            assert record["tool_results"][0]["failure_category"] == "docker_unavailable"
            assert "docker_unavailable" in record["final_response"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_tool_dispatcher_wraps_unexpected_exceptions() -> None:
    class ExplodingTool(BaseTool):
        spec = ToolSpec(
            name="explode",
            description="explode",
            input_schema={"type": "object", "required": [], "properties": {}, "additionalProperties": False},
            risk_level="low",
            sandbox_required=False,
            timeout=1,
            max_retries=0,
        )

        def execute(self, payload: dict[str, object]) -> ToolResult:
            raise RuntimeError("boom")

    app = MiniBotApp(ROOT)
    app.runtime.tool_dispatcher.registry.register(ExplodingTool())
    results, trace = app.runtime.tool_dispatcher.dispatch([{"tool_name": "explode", "arguments": {}}])
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "tool_dispatch_failed"
    assert trace[0]["status"] == "failed"


def test_tool_dispatcher_returns_structured_error_for_unknown_tool() -> None:
    app = MiniBotApp(ROOT)
    results, trace = app.runtime.tool_dispatcher.dispatch([{"tool_name": "missing_tool", "arguments": {}}])
    assert results[0]["success"] is False
    assert results[0]["failure_category"] == "tool_not_found"
    assert trace[0]["status"] == "failed"


def test_agent_loop_triggers_tool_calls_and_records_trace() -> None:
    temp_root = _prepare_temp_root()
    try:
        _write_policy(
            temp_root,
            {
                "approval": {
                    "auto_approve": True,
                    "tool_defaults": {"file_write": True, "memory_write": True},
                }
            },
        )
        app = MiniBotApp(temp_root)
        write_result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="tool-loop",
                content="write notes/result.txt content tool loop payload",
            )
        )
        write_path = app.runtime.workspace.runs_dir / f"{write_result.run_id}.json"
        write_record = json.loads(write_path.read_text(encoding="utf-8"))
        assert write_record["tool_calls"][0]["tool_name"] == "file_write"
        assert write_record["tool_results"][0]["status"] == "success"
        assert (app.runtime.workspace.sandbox_dir / "notes" / "result.txt").exists()

        read_result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="tool-loop", content="read notes/result.txt")
        )
        read_path = app.runtime.workspace.runs_dir / f"{read_result.run_id}.json"
        read_record = json.loads(read_path.read_text(encoding="utf-8"))
        assert read_record["tool_calls"][0]["tool_name"] == "file_read"
        assert read_record["tool_results"][0]["status"] == "success"

        memory_result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="tool-loop", content="记住 我喜欢中文回答")
        )
        memory_path = app.runtime.workspace.runs_dir / f"{memory_result.run_id}.json"
        memory_record = json.loads(memory_path.read_text(encoding="utf-8"))
        assert memory_record["tool_calls"][0]["tool_name"] == "memory_write"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
