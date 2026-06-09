from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path
from uuid import uuid4

from minibot import cli as minibot_cli
from minibot.governance.approval_store import ApprovalStore

ROOT = Path(__file__).resolve().parents[1]
HELLO = "\u4f60\u597d"
FEISHU_HELLO = "\u4f60\u597d\uff0cMiniBot"
MODEL_ENV_KEYS = (
    "MINIBOT_MODEL_MODE",
    "MINIBOT_MODEL_PROVIDER",
    "MINIBOT_MODEL_BASE_URL",
    "MINIBOT_MODEL_API_KEY",
    "MINIBOT_MODEL_NAME",
    "MINIBOT_VERIFIER_MODE",
    "MINIBOT_VERIFIER_PROVIDER",
    "MINIBOT_VERIFIER_BASE_URL",
    "MINIBOT_VERIFIER_API_KEY",
    "MINIBOT_VERIFIER_MODEL_NAME",
    "MINIBOT_BASE_URL",
    "MINIBOT_API_KEY",
)
EXTERNAL_ENV_KEYS = (
    "MINIBOT_WEATHER_PROVIDER",
    "MINIBOT_WEATHER_API_KEY",
    "MINIBOT_WEATHER_API_HOST",
    "MINIBOT_WEB_SEARCH_PROVIDER",
    "TAVILY_API_KEY",
    "TAVILY_PROJECT",
    "TAVILY_SEARCH_DEPTH",
    "TAVILY_MAX_RESULTS",
    "MINIBOT_MAP_PROVIDER",
    "MINIBOT_AMAP_MCP_ENDPOINT",
    "MINIBOT_AMAP_MCP_API_KEY",
)
FEISHU_ENV_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BOT_NAME",
    "FEISHU_BOT_MODE",
    "FEISHU_WS_ENABLED",
    "LARK_APP_ID",
    "LARK_APP_SECRET",
)


def _clean_model_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in (*MODEL_ENV_KEYS, *EXTERNAL_ENV_KEYS, *FEISHU_ENV_KEYS):
        env.pop(key, None)
    if overrides:
        env.update(overrides)
    return env


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minibot", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_model_env(),
        check=False,
    )


def run_cli_with_env(*args: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minibot", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_model_env(env_overrides),
        check=False,
    )


def run_cli_with_input(*args: str, stdin_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minibot", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=stdin_text,
        env=_clean_model_env(),
        check=False,
    )


def _prepare_temp_root() -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    return temp_root


def test_help_command_succeeds() -> None:
    completed = run_cli("--help")
    assert completed.returncode == 0
    assert "status" in completed.stdout
    assert "chat" in completed.stdout
    assert "feishu-mock" in completed.stdout


def test_status_command_returns_expected_checks() -> None:
    completed = run_cli("status")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["version"] == "0.1.0"
    assert payload["workspace_exists"] is True
    assert payload["memory_exists"] is True
    assert payload["history_exists"] is True
    assert payload["archives_dir_exists"] is True
    assert isinstance(payload["archive_count"], int)
    assert payload["benchmark_case_count"] >= 70
    assert payload["benchmark_case_count_by_profile"]["default"] >= 1
    assert payload["benchmark_case_count_by_category"]["tools"] >= 1
    # Phase 3: enhanced status fields
    assert "tasks_dir_exists" in payload
    assert "task_count" in payload
    assert "pending_task_count" in payload
    assert "approval_pending_count" in payload
    assert "budget" in payload
    assert payload["budget"]["max_tool_rounds"] >= 1
    assert payload["budget"]["max_tool_calls_total"] >= 1
    assert payload["budget"]["max_runtime_seconds"] >= 1
    assert payload["budget"]["max_same_tool_calls"] >= 1


def test_status_command_includes_budget_from_config() -> None:
    """The budget block in status reflects the configured agent profile."""
    completed = run_cli("status")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    budget = payload["budget"]
    assert isinstance(budget["agent_profile"], str)
    assert isinstance(budget["max_tool_rounds"], int)
    assert isinstance(budget["max_tool_calls_total"], int)
    assert isinstance(budget["max_runtime_seconds"], int)
    assert isinstance(budget["max_same_tool_calls"], int)


def test_chat_command_uses_agent_loop() -> None:
    completed = run_cli("chat", "--message", HELLO)
    assert completed.returncode == 0
    assert f"MiniBot echo: {HELLO}" in completed.stdout


def test_chat_command_persists_unicode_trace() -> None:
    completed = run_cli("chat", "--message", HELLO)
    assert completed.returncode == 0

    matching_runs = []
    for run_path in (ROOT / ".minibot" / "runs").glob("*.json"):
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        if (
            payload.get("user_input") == HELLO
            and payload.get("final_response") == f"MiniBot echo: {HELLO}"
        ):
            matching_runs.append((run_path, payload))
    assert matching_runs
    run_path, payload = max(matching_runs, key=lambda item: item[0].stat().st_mtime)
    assert payload["user_input"] == HELLO
    assert payload["final_response"] == f"MiniBot echo: {HELLO}"
    assert payload["input"] == HELLO
    assert payload["response"] == f"MiniBot echo: {HELLO}"


def test_chat_command_uses_harness_tool_path() -> None:
    completed = run_cli("chat", "--message", "请帮我计算 2 + 3")
    assert completed.returncode == 0
    assert "MiniBot tool result: 5" in completed.stdout

    matching_runs = []
    for run_path in (ROOT / ".minibot" / "runs").glob("*.json"):
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        if payload.get("user_input") == "请帮我计算 2 + 3":
            matching_runs.append((run_path, payload))
    assert matching_runs
    run_path, payload = max(matching_runs, key=lambda item: item[0].stat().st_mtime)
    assert payload["tool_calls"] == [
        {
            "tool_name": "calculator",
            "arguments": {"expression": "2 + 3"},
        }
    ]
    assert payload["final_response"] == "MiniBot tool result: 5"
    assert payload["model_plan"] == {
        "mode": "tool_call",
        "reason": "calculation_expression_detected",
        "tool_calls": [
            {
                "tool_name": "calculator",
                "arguments": {"expression": "2 + 3"},
            }
        ],
    }


def test_interactive_chat_exits_cleanly_on_eof() -> None:
    completed = run_cli_with_input("chat", stdin_text="calculate 2 + 3\n")
    assert completed.returncode == 0
    assert "MiniBot tool result: 5" in completed.stdout


def test_feishu_mock_accepts_positional_event_path() -> None:
    completed = run_cli("feishu-mock", "examples/mock_feishu_event.json")
    assert completed.returncode == 0
    assert f"MiniBot echo: {FEISHU_HELLO}" in completed.stdout


def test_feishu_mock_accepts_flag_event_path() -> None:
    completed = run_cli("feishu-mock", "--event", "examples/mock_feishu_event.json")
    assert completed.returncode == 0
    assert f"MiniBot echo: {FEISHU_HELLO}" in completed.stdout


def test_feishu_command_fails_with_missing_config() -> None:
    completed = run_cli("feishu")
    assert completed.returncode == 1
    assert "feishu_config_missing" in completed.stderr


def test_feishu_command_enters_long_running_channel_loop(monkeypatch, capsys) -> None:
    class _FakeRuntime:
        agent_loop = object()
        long_task_runner = None
        planner_agent = None

    class _FakeApp:
        runtime = _FakeRuntime()

    class _FakeChannel:
        def __init__(self) -> None:
            self.entered = False

        def run(self) -> dict[str, object]:
            self.entered = True
            raise KeyboardInterrupt()

    fake_channel = _FakeChannel()
    monkeypatch.setattr(minibot_cli, "MiniBotApp", lambda: _FakeApp())
    monkeypatch.setattr(
        minibot_cli.FeishuWebSocketChannel,
        "from_env",
        staticmethod(lambda agent_loop, **kw: fake_channel),
    )

    exit_code = minibot_cli.main(["feishu"])
    captured = capsys.readouterr()
    assert fake_channel.entered is True
    assert exit_code == 0
    assert "ready" not in captured.out


def test_approvals_list_outputs_pending_records(monkeypatch, capsys) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.chdir(temp_root)
        store = minibot_cli._load_approval_store()
        pending = store.create_pending(
            session_id="cli-list",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
            risk_level="gray",
            reason="approval_denied",
        )
        exit_code = minibot_cli.main(["approvals", "list"])
        captured = capsys.readouterr()
        assert exit_code == 0
        payload = json.loads(captured.out)
        assert payload[0]["approval_id"] == pending["approval_id"]
        assert payload[0]["status"] == "pending"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approvals_approve_moves_pending_to_resolved(monkeypatch, capsys) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.chdir(temp_root)
        store = minibot_cli._load_approval_store()
        pending = store.create_pending(
            session_id="cli-approve",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
            risk_level="gray",
            reason="approval_denied",
        )
        exit_code = minibot_cli.main(["approvals", "approve", str(pending["approval_id"])])
        captured = capsys.readouterr()
        assert exit_code == 0
        payload = json.loads(captured.out)
        assert payload["status"] == "approved"
        assert payload["action"] == "approved"
        resolved = ApprovalStore(store.root).find_resolution(
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
        )
        assert resolved is not None
        assert resolved["approval_id"] == pending["approval_id"]
        assert resolved["status"] == "approved"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approvals_reject_moves_pending_to_resolved(monkeypatch, capsys) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.chdir(temp_root)
        store = minibot_cli._load_approval_store()
        pending = store.create_pending(
            session_id="cli-reject",
            user_id="tester",
            tool_name="file_write",
            arguments={"path": "notes/demo.txt", "content": "hello"},
            risk_level="gray",
            reason="approval_denied",
        )
        exit_code = minibot_cli.main(["approvals", "reject", str(pending["approval_id"])])
        captured = capsys.readouterr()
        assert exit_code == 0
        payload = json.loads(captured.out)
        assert payload["status"] == "rejected"
        assert payload["action"] == "rejected"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_approvals_command_returns_nonzero_for_missing_id(monkeypatch, capsys) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.chdir(temp_root)
        exit_code = minibot_cli.main(["approvals", "approve", "missing-id"])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "approval_not_found" in captured.err
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_benchmark_reports_safety_results_and_metrics() -> None:
    completed = run_cli("benchmark", "--profile", "safety")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["phase"] == "phase1_skeleton"
    assert payload["run_mode"] == "fake"
    assert payload["fake_model"] is True
    assert payload["report_path"] == "reports/latest.json"
    assert payload["total_cases"] >= 9  # --profile safety
    assert payload["benchmark_case_count"] >= 70  # catalog counts all profiles
    assert payload.get("safety_case_count", 0) >= 1
    assert payload["benchmark_case_count_by_category"]["tools"] >= 1
    assert "tool_rounds" in payload
    assert "avg_latency" in payload
    assert any(result["category"] == "safety" for result in payload["results"])

    safety_result = next(
        result for result in payload["results"] if result["id"] == "safety_shell_block_001"
    )
    assert safety_result["status"] in {"passed", "failed"}
    assert safety_result["counted_in_pass_rate"] is True
    assert safety_result["failure_category"] == "blocked_by_policy"
    assert any(
        trace["tool_name"] == "shell_exec" and trace["status"] == "blocked"
        for trace in safety_result["tool_trace"]
    )


def test_benchmark_command_supports_explicit_fake_mode_report() -> None:
    completed = run_cli(
        "benchmark", "--mode", "fake", "--profile", "safety", "--report", "reports/run_fake_v1.json"
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["run_mode"] == "fake"
    assert payload["fake_model"] is True
    assert payload["model_provider"] == "fake"
    assert payload["extra_report_path"] == "reports/run_fake_v1.json"
    assert payload["external_integrations"]["web_fetch"] == "real"
    assert payload["external_integrations"]["web_search"] == "mock"


def test_benchmark_command_accepts_profile_argument() -> None:
    completed = run_cli("benchmark", "--mode", "fake", "--scope", "core", "--profile", "approval")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["benchmark_profile"] == "approval"


def test_benchmark_command_accepts_safety_profile_argument() -> None:
    completed = run_cli("benchmark", "--mode", "fake", "--scope", "core", "--profile", "safety")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["benchmark_profile"] == "safety"
    assert payload["safety_case_count"] >= 7


def test_benchmark_parser_accepts_real_agent_profile() -> None:
    args = minibot_cli.build_parser().parse_args(
        ["benchmark", "--mode", "real", "--scope", "core", "--profile", "real-agent"]
    )
    assert args.command == "benchmark"
    assert args.profile == "real-agent"
    assert args.mode == "real"


def test_benchmark_command_accepts_context_profiles() -> None:
    baseline = run_cli("benchmark", "--mode", "fake", "--profile", "context-baseline")
    assert baseline.returncode == 0
    baseline_payload = json.loads(baseline.stdout)
    assert baseline_payload["benchmark_profile"] == "context-baseline"
    assert "avg_prompt_tokens" in baseline_payload
    assert baseline_payload["token_estimator"] == "ceil_len_div_4"

    optimized = run_cli("benchmark", "--mode", "fake", "--profile", "context-optimized")
    assert optimized.returncode == 0
    optimized_payload = json.loads(optimized.stdout)
    assert optimized_payload["benchmark_profile"] == "context-optimized"
    assert "avg_prompt_tokens" in optimized_payload
    assert optimized_payload["token_estimator"] == "ceil_len_div_4"

    realistic_baseline = run_cli(
        "benchmark", "--mode", "fake", "--profile", "context-realistic-baseline"
    )
    assert realistic_baseline.returncode == 0
    realistic_baseline_payload = json.loads(realistic_baseline.stdout)
    assert realistic_baseline_payload["benchmark_profile"] == "context-realistic-baseline"

    realistic_optimized = run_cli(
        "benchmark", "--mode", "fake", "--profile", "context-realistic-optimized"
    )
    assert realistic_optimized.returncode == 0
    realistic_optimized_payload = json.loads(realistic_optimized.stdout)
    assert realistic_optimized_payload["benchmark_profile"] == "context-realistic-optimized"


def test_chat_command_nearby_hospital_still_routes_to_map_poi_search_not_web_search() -> None:
    completed = run_cli("chat", "--message", "帮我查一下厦门大学附近有什么医院")
    assert completed.returncode == 0

    matching_runs = []
    for run_path in (ROOT / ".minibot" / "runs").glob("*.json"):
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        if payload.get("user_input") == "帮我查一下厦门大学附近有什么医院":
            matching_runs.append((run_path, payload))
    assert matching_runs
    _, payload = max(matching_runs, key=lambda item: item[0].stat().st_mtime)
    assert payload["tool_calls"][0]["tool_name"] == "map_poi_search"
    assert payload["tool_calls"][0]["tool_name"] != "web_search"


def test_benchmark_command_real_mode_missing_config_generates_report_and_fails() -> None:
    report_path = ROOT / "reports" / "run_real_v1.json"
    if report_path.exists():
        report_path.unlink()
    completed = run_cli(
        "benchmark", "--mode", "real", "--scope", "core", "--report", "reports/run_real_v1.json"
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["run_mode"] == "real"
    assert payload["benchmark_scope"] == "core"
    assert payload["fake_model"] is False
    assert "deepseek_config_missing" in payload["missing_capabilities"]
    assert payload["capability_status"]["real_model"] == "missing"
    assert "deepseek_config_missing" in completed.stderr
    assert report_path.exists()


def test_benchmark_command_real_mode_http_error_generates_report_without_traceback(
    monkeypatch, capsys
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

    monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MINIBOT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    exit_code = minibot_cli.main(
        [
            "benchmark",
            "--mode",
            "real",
            "--scope",
            "core",
            "--report",
            "reports/run_real_http_error_v1.json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0 or exit_code == 1
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)
    assert payload["run_mode"] == "real"
    assert payload["fake_model"] is False
    assert payload["capability_status"]["real_model"] == "failed"
    assert payload["model_error"] == "model_http_error"


def test_compare_command_returns_failure_and_metric_deltas() -> None:
    benchmark = run_cli("benchmark", "--profile", "safety", "--report", "reports/run_v1.json")
    assert benchmark.returncode == 0
    completed = run_cli("compare", "reports/run_v1.json", "reports/run_v1.json")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["new_failures"] == []
    assert payload["fixed_failures"] == []
    assert "pass_rate" in payload["metric_changes"]
    assert "token_reduction_rate" in payload


def test_chat_command_can_trigger_retry_and_downgrade_weather() -> None:
    completed = run_cli("chat", "--message", "查询一个模拟失败的天气接口，并给我出行建议")
    assert completed.returncode == 0
    assert "MiniBot downgraded tool result:" in completed.stdout
    assert "fallback" in completed.stdout


def test_chat_command_can_trigger_partial_success_multi_tool_plan() -> None:
    completed = run_cli("chat", "--message", "shell_exec rm -rf /")
    assert completed.returncode == 0
    assert "MiniBot tool blocked:" in completed.stdout
    assert "blacklisted_command" in completed.stdout


def test_chat_command_weather_mock_and_real_mode_are_distinct(monkeypatch) -> None:
    before = {path.name for path in (ROOT / ".minibot" / "runs").glob("*.json")}
    monkeypatch.setenv("MINIBOT_WEATHER_PROVIDER", "mock")
    app = minibot_cli.MiniBotApp()
    channel = minibot_cli.CLIChannel(app.runtime.agent_loop)
    response = channel.send_once("weather Beijing")
    assert "Mock sunny" in response

    after = {path.name for path in (ROOT / ".minibot" / "runs").glob("*.json")}
    new_files = after - before
    assert new_files
    run_path = max(
        (ROOT / ".minibot" / "runs" / name for name in new_files),
        key=lambda path: path.stat().st_mtime,
    )
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    assert payload["tool_results"][0]["metadata"]["provider_status"] == "mock"
    assert payload["tool_results"][0]["metadata"]["mock_provider"] is True


def test_chat_command_smoke_triggers_python_exec() -> None:
    marker = uuid4().hex[:8]
    message = f"run python code print('{marker}')"
    completed = run_cli("chat", "--message", message)
    assert completed.returncode == 0
    assert "approval required" in completed.stdout.lower()
    assert "python_exec" in completed.stdout


def test_chat_command_smoke_triggers_shell_exec() -> None:
    completed = run_cli("chat", "--message", "shell_exec echo hello")
    assert completed.returncode == 0
    assert "approval required" in completed.stdout.lower()
    assert "shell_exec" in completed.stdout


def test_chat_command_routes_nearby_poi_queries_to_map_poi_search() -> None:
    completed = run_cli("chat", "--message", "帮我查一下厦门大学附近有什么医院")
    assert completed.returncode == 0

    matching_runs = []
    for run_path in (ROOT / ".minibot" / "runs").glob("*.json"):
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        if payload.get("user_input") == "帮我查一下厦门大学附近有什么医院":
            matching_runs.append((run_path, payload))
    assert matching_runs
    _, payload = max(matching_runs, key=lambda item: item[0].stat().st_mtime)
    assert payload["tool_calls"][0]["tool_name"] == "map_poi_search"
    assert payload["tool_results"][0]["metadata"]["provider"] == "map_poi_search"
    assert payload["tool_results"][0]["metadata"]["provider_status"] == "mock"
    assert payload["tool_calls"][0]["tool_name"] != "web_search"


def test_status_command_reports_real_mode_config_error_without_fake_fallback() -> None:
    completed = run_cli_with_env(
        "status",
        env_overrides={
            "MINIBOT_MODEL_MODE": "real",
            "MINIBOT_MODEL_PROVIDER": "deepseek",
            "MINIBOT_MODEL_BASE_URL": "https://api.deepseek.com",
            "MINIBOT_MODEL_NAME": "deepseek-chat",
            "MINIBOT_MODEL_API_KEY": "",
        },
    )
    assert completed.returncode == 1
    assert "deepseek_config_missing" in completed.stderr


def test_chat_command_fails_in_real_mode_when_model_config_missing_without_fake_fallback() -> None:
    completed = run_cli_with_env(
        "chat",
        "--message",
        "calculate 128 * 64",
        env_overrides={
            "MINIBOT_MODEL_MODE": "real",
        },
    )
    assert completed.returncode == 1
    assert "deepseek_config_missing" in completed.stderr
    assert "MiniBot tool result: 8192" not in completed.stdout


def test_new_command_fails_in_real_mode_when_model_config_missing_without_fake_summary_fallback() -> (
    None
):
    completed = run_cli_with_env(
        "chat",
        "--message",
        "/new",
        env_overrides={
            "MINIBOT_MODEL_MODE": "real",
        },
    )
    assert completed.returncode == 1
    assert "deepseek_config_missing" in completed.stderr


def test_chat_command_defaults_to_fake_even_if_host_has_real_env_markers() -> None:
    completed = run_cli_with_env(
        "chat",
        "--message",
        "calculate 2 + 3",
        env_overrides={
            "MINIBOT_MODEL_PROVIDER": "deepseek",
            "MINIBOT_MODEL_BASE_URL": "https://api.deepseek.com",
            "MINIBOT_MODEL_API_KEY": "host-key-should-not-be-used",
            "MINIBOT_MODEL_NAME": "deepseek-chat",
        },
    )
    assert completed.returncode == 0
    assert "MiniBot tool result: 5" in completed.stdout
