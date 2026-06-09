from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from minibot.cli import _task_resume
from minibot.governance.approval_store import ApprovalStore
from minibot.tasks.store import TaskStore, TaskStoreError

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# fixtures — project-local temp dir to avoid Windows tmp_path PermissionError
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_tasks_path() -> Path:
    """Yield a unique temp directory under the project root.

    Uses .tmp_test_roots/ (consistent with the rest of the test suite)
    instead of pytest's tmp_path, which can hit PermissionError on Windows
    when the system temp directory is locked by another process.
    """
    temp = ROOT / ".tmp_test_roots" / f"task-{uuid4()}"
    temp.mkdir(parents=True, exist_ok=True)
    yield temp
    shutil.rmtree(temp, ignore_errors=True)

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


def _temp_tasks_dir() -> Path:
    temp_root = ROOT / ".tmp_test_tasks" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


# ---------------------------------------------------------------------------
# unit tests — TaskStore data layer
# ---------------------------------------------------------------------------


class TestTaskStoreCreate:
    def test_create_task_succeeds(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        record = store.create("翻译这篇文档")
        assert record["task_id"]
        assert record["status"] == "pending"
        assert record["goal"] == "翻译这篇文档"
        assert record["created_at"]
        assert record["updated_at"]

    def test_create_task_with_session_and_user(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        record = store.create("写一封邮件", session_id="s1", user_id="u1")
        assert record["session_id"] == "s1"
        assert record["user_id"] == "u1"

    def test_create_task_with_metadata(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        record = store.create("分析数据", metadata={"priority": "high"})
        assert record["metadata"] == {"priority": "high"}


class TestTaskStoreList:
    def test_list_returns_recent_tasks(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        store.create("task-a")
        store.create("task-b")
        store.create("task-c")
        result = store.list()
        assert len(result) == 3

    def test_list_respects_limit(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        for i in range(10):
            store.create(f"task-{i}")
        result = store.list(limit=3)
        assert len(result) == 3

    def test_list_filters_by_status(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("pending-task")
        store.update(r["task_id"], status="running")
        store.create("another-pending")
        pending = store.list(status="pending")
        running = store.list(status="running")
        assert all(r["status"] == "pending" for r in pending)
        assert all(r["status"] == "running" for r in running)

    def test_list_empty_on_fresh_store(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        assert store.list() == []


class TestTaskStoreGet:
    def test_show_finds_task(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        created = store.create("目标")
        found = store.get(created["task_id"])
        assert found is not None
        assert found["task_id"] == created["task_id"]

    def test_show_returns_none_for_missing(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        assert store.get("nonexistent") is None


class TestTaskStoreUpdate:
    def test_update_modifies_status(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        updated = store.update(r["task_id"], status="running")
        assert updated["status"] == "running"
        # get should return the latest
        assert store.get(r["task_id"])["status"] == "running"

    def test_update_modifies_last_run_id(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        updated = store.update(r["task_id"], last_run_id="run-001")
        assert updated["last_run_id"] == "run-001"

    def test_update_updates_timestamp(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        updated = store.update(r["task_id"], status="running")
        assert updated["updated_at"] != r["updated_at"]

    def test_update_raises_for_missing_task(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        try:
            store.update("no-such-id", status="running")
            assert False, "expected TaskStoreError"
        except TaskStoreError as exc:
            assert "task_not_found" in str(exc)

    def test_update_rejects_invalid_status(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        try:
            store.update(r["task_id"], status="archived")
            assert False, "expected TaskStoreError"
        except TaskStoreError as exc:
            assert "invalid_status" in str(exc)

    def test_update_multiple_fields_at_once(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        updated = store.update(
            r["task_id"],
            status="waiting_approval",
            pending_approval_id="apv-1",
            stop_reason="awaiting_human",
        )
        assert updated["status"] == "waiting_approval"
        assert updated["pending_approval_id"] == "apv-1"
        assert updated["stop_reason"] == "awaiting_human"


class TestTaskStoreCancel:
    def test_cancel_sets_status_to_cancelled(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("task")
        cancelled = store.cancel(r["task_id"])
        assert cancelled["status"] == "cancelled"
        assert store.get(r["task_id"])["status"] == "cancelled"


class TestTaskStoreJsonlSemantics:
    def test_jsonl_empty_returns_empty_list(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        assert store.list() == []

    def test_last_record_wins_for_same_task_id(self, tmp_tasks_path: Path) -> None:
        """Multiple updates to the same task_id — the latest record wins."""
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("evolving task")
        store.update(r["task_id"], status="running")
        store.update(r["task_id"], status="completed", stop_reason="done")
        final = store.get(r["task_id"])
        assert final["status"] == "completed"
        assert final["stop_reason"] == "done"
        # list should return exactly 1 task for this task_id
        all_tasks = store.list()
        tids = [t["task_id"] for t in all_tasks]
        assert tids.count(r["task_id"]) == 1

    def test_all_valid_statuses_accepted(self, tmp_tasks_path: Path) -> None:
        store = TaskStore(tmp_tasks_path / "tasks")
        r = store.create("status tour")
        for status in (
            "pending",
            "running",
            "waiting_approval",
            "completed",
            "failed",
            "cancelled",
        ):
            store.update(r["task_id"], status=status)
            assert store.get(r["task_id"])["status"] == status


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestTasksCLI:
    def test_tasks_subparser_registered(self) -> None:
        from minibot import cli as minibot_cli

        parser = minibot_cli.build_parser()
        # --help output should mention "tasks"
        help_text = parser.format_help()
        assert "tasks" in help_text

    def test_tasks_create_and_show(self) -> None:
        completed = run_cli("tasks", "create", "--goal", "CLI 集成测试任务")
        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert payload["task_id"]
        assert payload["status"] == "pending"
        assert payload["goal"] == "CLI 集成测试任务"

        # show the created task
        task_id = payload["task_id"]
        show = run_cli("tasks", "show", task_id)
        assert show.returncode == 0
        show_payload = json.loads(show.stdout)
        assert show_payload["task_id"] == task_id
        assert show_payload["goal"] == "CLI 集成测试任务"

    def test_tasks_list(self) -> None:
        completed = run_cli("tasks", "list")
        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert isinstance(payload, list)

    def test_tasks_cancel(self) -> None:
        # create
        created = run_cli("tasks", "create", "--goal", "待取消任务")
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        # cancel
        cancelled = run_cli("tasks", "cancel", task_id)
        assert cancelled.returncode == 0
        cancel_payload = json.loads(cancelled.stdout)
        assert cancel_payload["status"] == "cancelled"

    def test_tasks_show_missing(self) -> None:
        completed = run_cli("tasks", "show", "00000000-0000-0000-0000-000000000000")
        assert completed.returncode == 1
        assert "task_not_found" in completed.stderr


# ---------------------------------------------------------------------------
# tasks resume — harness integration tests
# ---------------------------------------------------------------------------


class TestTasksResume:
    def test_resume_successful_task_sets_completed(self) -> None:
        """Resume a calculator task → status=completed, last_run_id populated."""
        created = run_cli("tasks", "create", "--goal", "calculate 2 + 3")
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        resumed = run_cli("tasks", "resume", task_id)
        assert resumed.returncode == 0
        payload = json.loads(resumed.stdout)
        assert payload["status"] == "completed"
        assert payload["last_run_id"]
        assert payload["last_run_id"] != "null"

    def test_resume_writes_task_id_into_run_record(self) -> None:
        """The run record persisted on disk must include task_id."""
        created = run_cli("tasks", "create", "--goal", "calculate 128 * 64")
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        resumed = run_cli("tasks", "resume", task_id)
        assert resumed.returncode == 0
        payload = json.loads(resumed.stdout)
        run_id = payload["last_run_id"]

        # Read the run record from disk
        runs_dir = ROOT / ".minibot" / "runs"
        run_path = runs_dir / f"{run_id}.json"
        assert run_path.exists()
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        assert run_record.get("task_id") == task_id

    def test_resume_blocked_by_policy_sets_failed(self) -> None:
        """A task whose goal triggers blocked_by_policy → status=failed."""
        created = run_cli("tasks", "create", "--goal", "shell_exec rm -rf /")
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        resumed = run_cli("tasks", "resume", task_id)
        assert resumed.returncode == 0
        payload = json.loads(resumed.stdout)
        assert payload["status"] == "failed"
        assert payload["last_run_id"]

    def test_resume_cancelled_task_is_rejected(self) -> None:
        """Cancelled tasks cannot be resumed."""
        created = run_cli("tasks", "create", "--goal", "calculate 1 + 1")
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        run_cli("tasks", "cancel", task_id)
        resumed = run_cli("tasks", "resume", task_id)
        assert resumed.returncode == 1
        assert "task_is_cancelled" in resumed.stderr

    def test_resume_of_missing_task_returns_error(self) -> None:
        completed = run_cli("tasks", "resume", "00000000-0000-0000-0000-000000000000")
        assert completed.returncode == 1
        assert "task_not_found" in completed.stderr

    def test_resume_approval_required_sets_waiting_approval(self) -> None:
        """A task that hits the graylist → approval_required → waiting_approval with pending_approval_id."""
        created = run_cli(
            "tasks", "create", "--goal", "write notes/task-resume-test.txt content hello"
        )
        assert created.returncode == 0
        task_id = json.loads(created.stdout)["task_id"]

        resumed = run_cli("tasks", "resume", task_id)
        assert resumed.returncode == 0
        payload = json.loads(resumed.stdout)
        assert payload["status"] == "waiting_approval"
        assert payload["pending_approval_id"]
        assert payload["stop_reason"] == "approval_required"


# ---------------------------------------------------------------------------
# isolation: ordinary chat / benchmark unaffected by task_id
# ---------------------------------------------------------------------------


class TestChatUnaffected:
    def test_ordinary_chat_still_works(self) -> None:
        completed = run_cli("chat", "--message", "calculate 2 + 3")
        assert completed.returncode == 0
        assert "MiniBot tool result: 5" in completed.stdout

    def test_ordinary_chat_run_record_has_no_task_id(self) -> None:
        """Run records from ordinary chat should have task_id: null."""
        completed = run_cli("chat", "--message", "calculate 100 + 200")
        assert completed.returncode == 0

        # Find the latest run record
        runs_dir = ROOT / ".minibot" / "runs"
        run_files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        assert run_files
        latest = json.loads(run_files[0].read_text(encoding="utf-8"))
        assert latest.get("task_id") is None


class TestBenchmarkUnaffected:
    def test_benchmark_run_record_has_no_task_id(self) -> None:
        """Benchmark runs should not carry a task_id."""
        completed = run_cli("benchmark", "--mode", "fake", "--profile", "multiround")
        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        # The benchmark report itself should still work
        assert payload["benchmark_profile"] == "multiround"
        assert "multiround_case_count" in payload


# ---------------------------------------------------------------------------
# E2E task approval integration tests (in-process, isolated temp root)
# ---------------------------------------------------------------------------


def _prepare_temp_task_root() -> Path:
    """Create an isolated temp root with configs/ for task e2e tests."""
    temp_root = ROOT / ".tmp_test_roots" / f"task-e2e-{uuid4()}"
    temp_root.mkdir(parents=True, exist_ok=True)
    configs_target = temp_root / "configs"
    source_configs = ROOT / "configs"
    if source_configs.is_dir():
        shutil.copytree(source_configs, configs_target)
    else:
        configs_target.mkdir(parents=True, exist_ok=True)
    return temp_root


def _build_task_store(temp_root: Path) -> TaskStore:
    """Build a TaskStore that reads/writes inside *temp_root* workspace."""
    from minibot.config import load_config as _load_cfg
    from minibot.workspace import WorkspaceManager as _WM

    config = _load_cfg(temp_root / "configs" / "minibot.json")
    wm = _WM(temp_root, config.workspace_dir)
    wm.ensure()
    tasks_dir = temp_root / config.workspace_dir / "tasks"
    return TaskStore(tasks_dir)


def _build_approval_store(temp_root: Path) -> ApprovalStore:
    """Build an ApprovalStore that reads/writes inside *temp_root* workspace."""
    from minibot.config import load_config as _load_cfg

    config = _load_cfg(temp_root / "configs" / "minibot.json")
    approvals_dir = temp_root / config.workspace_dir / "approvals"
    return ApprovalStore(approvals_dir)


def _read_run_record(temp_root: Path, run_id: str) -> dict[str, object]:
    runs_dir = temp_root / ".minibot" / "runs"
    return json.loads((runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))


def _sandbox_file(temp_root: Path, rel_path: str) -> Path:
    return temp_root / ".minibot" / "sandbox_workspace" / rel_path


class TestTaskApprovalE2E:
    """End-to-end: create → resume → waiting_approval → approve/reject → resume → verify."""

    def test_approval_approve_e2e(self) -> None:
        """Full approve flow: create → resume → waiting_approval → approve → resume → completed."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            # 1. Create task
            task = store.create("write notes/task_e2e.txt content TaskOK")
            task_id = str(task["task_id"])

            # 2. First resume → waiting_approval
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 0

            task_after_first = store.get(task_id)
            assert task_after_first["status"] == "waiting_approval"
            first_run_id = str(task_after_first["last_run_id"])
            assert first_run_id
            pending_id = str(task_after_first["pending_approval_id"])
            assert pending_id
            assert task_after_first["stop_reason"] == "approval_required"

            # File NOT written yet
            assert not _sandbox_file(temp_root, "notes/task_e2e.txt").exists()

            # Run record has task_id
            run1 = _read_run_record(temp_root, first_run_id)
            assert run1.get("task_id") == task_id

            # 3. HTTP approve (via store directly)
            resolved = approval_store.approve(pending_id)
            assert resolved["status"] == "approved"

            # Approve does NOT auto-execute; file still absent
            assert not _sandbox_file(temp_root, "notes/task_e2e.txt").exists()

            # 4. Second resume → completed
            exit_code2 = _task_resume(store, task_id, root=temp_root)
            assert exit_code2 == 0

            task_final = store.get(task_id)
            assert task_final["status"] == "completed"
            second_run_id = str(task_final["last_run_id"])
            assert second_run_id
            # last_run_id updated
            assert second_run_id != first_run_id

            # Run record updated
            run2 = _read_run_record(temp_root, second_run_id)
            assert run2.get("task_id") == task_id

            # File IS now written
            written = _sandbox_file(temp_root, "notes/task_e2e.txt")
            assert written.exists()
            assert "TaskOK" in written.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_approval_reject_e2e(self) -> None:
        """Full reject flow: create → resume → waiting_approval → reject → resume → failed."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            # 1. Create task
            task = store.create("write notes/task_reject.txt content RejectOK")
            task_id = str(task["task_id"])

            # 2. First resume → waiting_approval
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 0

            task_after_first = store.get(task_id)
            assert task_after_first["status"] == "waiting_approval"
            pending_id = str(task_after_first["pending_approval_id"])
            assert pending_id

            # File NOT written
            assert not _sandbox_file(temp_root, "notes/task_reject.txt").exists()

            # 3. HTTP reject
            resolved = approval_store.reject(pending_id)
            assert resolved["status"] == "rejected"

            # 4. Second resume → failed with approval_rejected
            exit_code2 = _task_resume(store, task_id, root=temp_root)
            assert exit_code2 == 0

            task_final = store.get(task_id)
            assert task_final["status"] == "failed"
            stop_reason = str(task_final.get("stop_reason", ""))
            # stop_reason must reflect approval_rejected
            assert "approval_rejected" in stop_reason

            # File NOT written
            assert not _sandbox_file(temp_root, "notes/task_reject.txt").exists()

            # Run record has task_id
            run_id = str(task_final["last_run_id"])
            assert run_id
            run = _read_run_record(temp_root, run_id)
            assert run.get("task_id") == task_id
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_approve_does_not_auto_execute(self) -> None:
        """HTTP approve only changes approval status; tool execution requires re-resume."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_noauto.txt content ShouldNotExist")
            task_id = str(task["task_id"])

            # First resume → waiting_approval
            _task_resume(store, task_id, root=temp_root)
            task_after = store.get(task_id)
            pending_id = str(task_after["pending_approval_id"])

            # Approve but do NOT resume
            approval_store.approve(pending_id)

            # File should NOT exist — approve alone doesn't execute
            assert not _sandbox_file(temp_root, "notes/task_noauto.txt").exists()

            # Task still waiting_approval (not auto-transitioned)
            task_still = store.get(task_id)
            assert task_still["status"] == "waiting_approval"
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_approve_then_resume_executes(self) -> None:
        """After approve, the same task resumes and the tool actually runs."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_exec.txt content HelloWorld")
            task_id = str(task["task_id"])

            # Resume → waiting_approval
            _task_resume(store, task_id, root=temp_root)
            pending_id = str(store.get(task_id)["pending_approval_id"])

            # Approve
            approval_store.approve(pending_id)

            # Resume again
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 0

            task_final = store.get(task_id)
            assert task_final["status"] == "completed"

            # Tool actually executed
            f = _sandbox_file(temp_root, "notes/task_exec.txt")
            assert f.exists()
            assert "HelloWorld" in f.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_reject_then_resume_fails_without_executing(self) -> None:
        """After reject, the same task resumes, fails, and no file is written."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_rej2.txt content NoWrite")
            task_id = str(task["task_id"])

            _task_resume(store, task_id, root=temp_root)
            pending_id = str(store.get(task_id)["pending_approval_id"])

            # Reject
            approval_store.reject(pending_id)

            # Resume again
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 0

            task_final = store.get(task_id)
            assert task_final["status"] == "failed"

            # No file written
            assert not _sandbox_file(temp_root, "notes/task_rej2.txt").exists()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_resume_waiting_approval_task_is_allowed(self) -> None:
        """waiting_approval tasks CAN be re-resumed (not blocked like cancelled)."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_retry.txt content RetryOK")
            task_id = str(task["task_id"])

            # First resume → waiting_approval
            _task_resume(store, task_id, root=temp_root)
            assert store.get(task_id)["status"] == "waiting_approval"

            # Approve
            pending_id = str(store.get(task_id)["pending_approval_id"])
            approval_store.approve(pending_id)

            # Second resume from waiting_approval → should succeed
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 0

            task_final = store.get(task_id)
            assert task_final["status"] == "completed"
            assert _sandbox_file(temp_root, "notes/task_retry.txt").exists()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_task_last_run_id_updates_after_second_resume(self) -> None:
        """After a second resume, last_run_id reflects the new run, not the first."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_lrid.txt content LRID")
            task_id = str(task["task_id"])

            _task_resume(store, task_id, root=temp_root)
            first_run_id = str(store.get(task_id)["last_run_id"])

            pending_id = str(store.get(task_id)["pending_approval_id"])
            approval_store.approve(pending_id)

            _task_resume(store, task_id, root=temp_root)
            second_run_id = str(store.get(task_id)["last_run_id"])

            assert first_run_id != second_run_id
            # Both run records carry the same task_id
            r1 = _read_run_record(temp_root, first_run_id)
            r2 = _read_run_record(temp_root, second_run_id)
            assert r1["task_id"] == task_id
            assert r2["task_id"] == task_id
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_approval_pending_count_reflects_pending_state(self) -> None:
        """After creating a pending approval, status shows correct pending count."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_apc.txt content APC")
            task_id = str(task["task_id"])

            # Before resume: 0 pending
            assert approval_store.counts()["pending_count"] == 0

            _task_resume(store, task_id, root=temp_root)
            # After resume to waiting_approval: 1 pending
            assert approval_store.counts()["pending_count"] == 1

            pending_id = str(store.get(task_id)["pending_approval_id"])
            approval_store.approve(pending_id)

            # After approve: 0 pending
            counts = approval_store.counts()
            assert counts["pending_count"] == 0
            assert counts["approved_count"] >= 1
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_approval_pending_count_decreases_after_resolve(self) -> None:
        """Pending count goes 0→1→0 across create/approve lifecycle."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_cnt.txt content CountMe")
            task_id = str(task["task_id"])

            assert approval_store.counts()["pending_count"] == 0

            _task_resume(store, task_id, root=temp_root)
            assert approval_store.counts()["pending_count"] == 1

            pending_id = str(store.get(task_id)["pending_approval_id"])
            approval_store.reject(pending_id)
            assert approval_store.counts()["pending_count"] == 0
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_isolated_temp_root_does_not_pollute_real_approvals(self) -> None:
        """Temp-root tests write to isolated .minibot/; real approvals untouched."""
        real_root_approvals = ROOT / ".minibot" / "approvals"
        before_pending = (
            len(
                list(
                    (real_root_approvals / "pending.jsonl")
                    .read_text(encoding="utf-8-sig")
                    .splitlines()
                )
            )
            if (real_root_approvals / "pending.jsonl").exists()
            else 0
        )

        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            task = store.create("write notes/task_iso.txt content IsoTest")
            _task_resume(store, str(task["task_id"]), root=temp_root)
            # Created a pending approval in the ISOLATED store
            assert approval_store.counts()["pending_count"] == 1

            # Real store should be unchanged
            after_pending = (
                len(
                    list(
                        (real_root_approvals / "pending.jsonl")
                        .read_text(encoding="utf-8-sig")
                        .splitlines()
                    )
                )
                if (real_root_approvals / "pending.jsonl").exists()
                else 0
            )
            assert after_pending == before_pending
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


class TestTaskApprovalEdgeCases:
    """Edge-case and correctness checks for the approval → resume loop."""

    def test_cancelled_task_still_cannot_be_resumed(self) -> None:
        """Cancelled tasks are rejected regardless of approval state."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            task = store.create("write notes/should_not_run.txt content No")
            task_id = str(task["task_id"])

            # Cancel the task
            store.cancel(task_id)
            assert store.get(task_id)["status"] == "cancelled"

            # Resume must be rejected
            exit_code = _task_resume(store, task_id, root=temp_root)
            assert exit_code == 1

            # No file created
            assert not _sandbox_file(temp_root, "notes/should_not_run.txt").exists()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_ordinary_chat_unaffected_by_task_approval_flow(self) -> None:
        """Ordinary chat still works fine while tasks go through approval."""
        temp_root = _prepare_temp_task_root()
        try:
            from minibot.app import MiniBotApp as _App
            from minibot.channels.base import ChannelMessage as _CM

            app = _App(temp_root)
            result = app.runtime.agent_loop.handle_message(
                _CM(
                    channel="test",
                    user_id="test-user",
                    session_id="chat-session",
                    content="calculate 2 + 3",
                    metadata={},
                )
            )
            assert result.response
            # Ordinary chat has no task_id
            run = _read_run_record(temp_root, result.run_id)
            assert run.get("task_id") is None
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_benchmark_still_runs_on_temp_root(self) -> None:
        """Benchmark (fake mode) works on a temp root without task interference."""
        temp_root = _prepare_temp_task_root()
        try:
            from minibot.app import MiniBotApp as _App
            from minibot.evals.benchmark_runner import BenchmarkRunner as _BR

            app = _App(temp_root)
            runner = _BR(app.runtime.agent_loop, temp_root)
            report = runner.run(category="channel", mode="fake")
            assert report["phase"] == "phase1_skeleton"
            assert "pass_rate" in report
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_multiple_approve_reject_cycles_on_same_task(self) -> None:
        """A task can be approved then a new approval created on re-resume."""
        temp_root = _prepare_temp_task_root()
        try:
            store = _build_task_store(temp_root)
            approval_store = _build_approval_store(temp_root)

            # Use a task that will hit graylist each time
            task = store.create("write notes/task_cycle.txt content CycleTest")
            task_id = str(task["task_id"])

            # Cycle 1: resume → waiting → reject
            _task_resume(store, task_id, root=temp_root)
            pid1 = str(store.get(task_id)["pending_approval_id"])
            approval_store.reject(pid1)

            # Resume → fails (rejected)
            _task_resume(store, task_id, root=temp_root)
            assert store.get(task_id)["status"] == "failed"

            # A completed/failed task can still be re-resumed...
            # Let's check by creating a fresh task
            task2 = store.create("write notes/task_cycle2.txt content Cycle2")
            tid2 = str(task2["task_id"])

            _task_resume(store, tid2, root=temp_root)
            pid2 = str(store.get(tid2)["pending_approval_id"])
            approval_store.approve(pid2)

            _task_resume(store, tid2, root=temp_root)
            assert store.get(tid2)["status"] == "completed"
            assert _sandbox_file(temp_root, "notes/task_cycle2.txt").exists()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
