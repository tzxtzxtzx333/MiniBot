"""Command-line interface for MiniBot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import __version__
from .app import MiniBotApp
from .channels.base import ChannelMessage
from .channels.cli_channel import CLIChannel
from .channels.feishu_ws_channel import FeishuWebSocketChannel
from .channels.http_channel import HttpChannel
from .channels.mock_feishu_channel import MockFeishuChannel
from .config import load_config
from .evals.compare_reports import ReportComparator
from .evidence.store import EvidenceStore
from .governance.approval_store import ApprovalStore, ApprovalStoreError
from .tasks.store import TaskStore, TaskStoreError
from .workspace import WorkspaceManager


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level MiniBot CLI parser."""

    parser = argparse.ArgumentParser(prog="python -m minibot", description="MiniBot local assistant")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show runtime status")

    chat_parser = subparsers.add_parser("chat", help="Start chat mode")
    chat_parser.add_argument("--message", help="Single-turn message for non-interactive usage")

    http_parser = subparsers.add_parser("http", help="Run the HTTP channel server")
    http_parser.add_argument("--host", help="Host override")
    http_parser.add_argument("--port", type=int, help="Port override")

    feishu_parser = subparsers.add_parser("feishu-mock", help="Run a mock Feishu event through AgentLoop")
    feishu_parser.add_argument("event_path", nargs="?", help="Path to mock event JSON")
    feishu_parser.add_argument("--event", default="examples/mock_feishu_event.json", help="Path to mock event JSON")
    feishu_parser.add_argument("--message", help="Inline message text for quick testing (skips JSON file)")
    subparsers.add_parser("feishu", help="Run the real Feishu WebSocket Bot adapter boundary")

    approvals_parser = subparsers.add_parser("approvals", help="Manage pending human-review approvals")
    approvals_subparsers = approvals_parser.add_subparsers(dest="approvals_command")
    approvals_subparsers.add_parser("list", help="List pending approvals")
    approve_parser = approvals_subparsers.add_parser("approve", help="Approve one pending request")
    approve_parser.add_argument("approval_id")
    reject_parser = approvals_subparsers.add_parser("reject", help="Reject one pending request")
    reject_parser.add_argument("approval_id")

    tasks_parser = subparsers.add_parser("tasks", help="Manage task state")
    tasks_subparsers = tasks_parser.add_subparsers(dest="tasks_command")
    tasks_create_parser = tasks_subparsers.add_parser("create", help="Create a new task")
    tasks_create_parser.add_argument("--goal", required=True, help="Task goal description")
    tasks_create_parser.add_argument("--session", help="Optional session id")
    tasks_create_parser.add_argument("--user", help="Optional user id")
    tasks_subparsers.add_parser("list", help="List recent tasks")
    tasks_show_parser = tasks_subparsers.add_parser("show", help="Show one task by id")
    tasks_show_parser.add_argument("task_id")
    tasks_cancel_parser = tasks_subparsers.add_parser("cancel", help="Cancel a task")
    tasks_cancel_parser.add_argument("task_id")
    tasks_resume_parser = tasks_subparsers.add_parser("resume", help="Resume a task through AgentLoop")
    tasks_resume_parser.add_argument("task_id")

    benchmark_parser = subparsers.add_parser("benchmark", help="Run JSON benchmark cases")
    benchmark_parser.add_argument("--category", help="Optional benchmark category")
    benchmark_parser.add_argument("--scope", help="Optional benchmark scope, for example core")
    benchmark_parser.add_argument(
        "--profile",
        choices=[
            "default",
            "approval",
            "execution",
            "all-integrations",
            "real-agent",
            "safety",
            "context-baseline",
            "context-optimized",
            "context-realistic-baseline",
            "context-realistic-optimized",
            "multiround",
            "planner",
        ],
        default="default",
        help="Optional benchmark profile",
    )
    benchmark_parser.add_argument("--mode", choices=["fake", "real"], default="fake", help="Benchmark execution mode")
    benchmark_parser.add_argument("--report", help="Optional report path")

    compare_parser = subparsers.add_parser("compare", help="Compare two benchmark reports")
    compare_parser.add_argument("left", nargs="?", help="Left report path")
    compare_parser.add_argument("right", nargs="?", help="Right report path")

    evidence_parser = subparsers.add_parser("evidence", help="Manage evidence records")
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command")
    evidence_subparsers.add_parser("list", help="List recent evidence records")
    evidence_show_parser = evidence_subparsers.add_parser("show", help="Show one evidence record")
    evidence_show_parser.add_argument("evidence_id")
    evidence_search_parser = evidence_subparsers.add_parser("search", help="Search evidence by keyword")
    evidence_search_parser.add_argument("query")

    plan_parser = subparsers.add_parser("plan", help="Task plan management")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command")
    plan_create_parser = plan_subparsers.add_parser("create", help="Create a new plan from a goal")
    plan_create_parser.add_argument("--goal", required=True, help="User goal to decompose into steps")
    plan_run_parser = plan_subparsers.add_parser("run", help="Execute a plan")
    plan_run_parser.add_argument("plan_id")
    plan_show_parser = plan_subparsers.add_parser("show", help="Show plan details")
    plan_show_parser.add_argument("plan_id")
    plan_resume_parser = plan_subparsers.add_parser("resume", help="Resume a paused plan")
    plan_resume_parser.add_argument("plan_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return an exit code."""

    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "compare":
        return _run_compare(args.left, args.right)
    if args.command == "benchmark":
        return _run_benchmark(args.category, args.scope, args.profile, args.mode, args.report)
    if args.command == "approvals":
        return _run_approvals(args.approvals_command, getattr(args, "approval_id", None))
    if args.command == "tasks":
        return _run_tasks(args)
    if args.command == "evidence":
        return _run_evidence(args)
    if args.command == "plan":
        return _run_plan(args)

    try:
        app = MiniBotApp()
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command == "status":
        return _run_status(app)
    if args.command == "chat":
        return _run_chat(app, args.message)
    if args.command == "http":
        return _run_http(app, args.host, args.port)
    if args.command == "feishu-mock":
        return _run_feishu_mock(app, args)
    if args.command == "feishu":
        return _run_feishu(app)
    parser.print_help()
    return 0


def _run_status(app: MiniBotApp) -> int:
    report = app.runtime.status_service.collect()
    print(report.to_json())
    return 0


def _run_chat(app: MiniBotApp, message: str | None) -> int:
    channel = CLIChannel(app.runtime.agent_loop)
    if message is not None:
        print(channel.send_once(message))
        return 0

    print("MiniBot chat mode. Type 'exit' to quit.")
    while True:
        try:
            content = input("you> ").strip()
        except EOFError:
            return 0
        if content.lower() in {"exit", "quit"}:
            return 0
        print(channel.send_once(content))


def _run_http(app: MiniBotApp, host: str | None, port: int | None) -> int:
    approval_store = _load_approval_store()
    auth_token = os.environ.get("MINIBOT_HTTP_AUTH_TOKEN", "").strip() or None
    channel = HttpChannel(
        agent_loop=app.runtime.agent_loop,
        status_service=app.runtime.status_service,
        benchmark_runner=app.runtime.benchmark_runner,
        approval_store=approval_store,
        auth_token=auth_token,
    )
    channel.run(host or app.runtime.config.http.host, port or app.runtime.config.http.port)
    return 0


def _run_feishu_mock(app: MiniBotApp, args: argparse.Namespace) -> int:
    channel = MockFeishuChannel(
        app.runtime.agent_loop,
        long_task_runner=app.runtime.long_task_runner,
        planner_agent=app.runtime.planner_agent,
    )
    # Inline --message mode: bypass JSON file
    inline_message = getattr(args, "message", None)
    if inline_message:
        message = ChannelMessage(
            channel="feishu_mock",
            user_id="feishu-user",
            session_id="feishu-plan-test",
            content=inline_message,
            metadata={"message_id": "inline-test"},
        )
        plan_reply = channel.dispatch_plan(message)
        if plan_reply is not None:
            print(plan_reply)
        else:
            print(channel.dispatch_message(message).response)
        return 0
    event_path = _resolve_event_path(args)
    print(channel.run_event_file(event_path))
    return 0


def _run_feishu(app: MiniBotApp) -> int:
    channel = FeishuWebSocketChannel.from_env(
        agent_loop=app.runtime.agent_loop,
        long_task_runner=app.runtime.long_task_runner,
        planner_agent=app.runtime.planner_agent,
    )
    try:
        status = channel.run()
    except KeyboardInterrupt:
        print("feishu_stopped", file=sys.stderr)
        return 0
    if status.get("status") == "failed":
        print(str(status.get("error") or "feishu_failed"), file=sys.stderr)
        return 1
    if status.get("status") not in {"stopped"}:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def _run_benchmark(category: str | None, scope: str | None, profile: str, mode: str, report_path: str | None) -> int:
    try:
        app = _build_benchmark_app(mode)
        report = app.runtime.benchmark_runner.run(
            category=category,
            scope=scope,
            profile=profile,
            mode=mode,
            report_path=Path(report_path) if report_path else None,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if mode == "real" and report.get("missing_capabilities"):
        print(", ".join(report["missing_capabilities"]), file=sys.stderr)
        return 1
    return 0


def _run_compare(left: str | None, right: str | None) -> int:
    comparator = ReportComparator()
    result = comparator.compare(
        Path(left) if left else Path("reports/latest.json"),
        Path(right) if right else Path("reports/latest.json"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_approvals(command: str | None, approval_id: str | None) -> int:
    store = _load_approval_store()
    if command == "list":
        print(json.dumps(store.list_pending(), ensure_ascii=False, indent=2))
        return 0
    if command == "approve":
        try:
            record = store.approve(str(approval_id))
        except ApprovalStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    if command == "reject":
        try:
            record = store.reject(str(approval_id))
        except ApprovalStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    print("approvals_command_missing", file=sys.stderr)
    return 1


def _run_evidence(args: argparse.Namespace) -> int:
    store = _load_evidence_store()
    command = args.evidence_command
    if command is None:
        print("evidence_command_missing", file=sys.stderr)
        return 1

    if command == "list":
        records = store.list(limit=50)
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    if command == "show":
        evidence_id = getattr(args, "evidence_id", "")
        record = store.get(evidence_id)
        if record is None:
            print(f"evidence_not_found: {evidence_id}", file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if command == "search":
        query = getattr(args, "query", "")
        results = store.search(query, top_k=10)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print(f"unknown_evidence_command: {command}", file=sys.stderr)
    return 1


def _run_plan(args: argparse.Namespace) -> int:
    command = args.plan_command
    if command is None:
        print("plan_command_missing (create | run | show | resume)", file=sys.stderr)
        return 1

    try:
        app = MiniBotApp()
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if command == "create":
        plan = app.runtime.planner_agent.plan(args.goal)
        app.runtime.task_executor.save_plan(plan)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if command == "show":
        plan = app.runtime.task_executor.load_plan(args.plan_id)
        if plan is None:
            print(f"plan_not_found: {args.plan_id}", file=sys.stderr)
            return 1
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if command == "run":
        plan = app.runtime.task_executor.load_plan(args.plan_id)
        if plan is None:
            print(f"plan_not_found: {args.plan_id}", file=sys.stderr)
            return 1
        result = app.runtime.long_task_runner.run(plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"completed", "waiting_approval"} else 1

    if command == "resume":
        result = app.runtime.long_task_runner.resume(args.plan_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "error":
            return 1
        return 0 if result["status"] in {"completed", "waiting_approval"} else 1

    print(f"unknown_plan_command: {command}", file=sys.stderr)
    return 1


def _load_evidence_store() -> EvidenceStore:
    root = Path.cwd().resolve()
    config = load_config(root / "configs" / "minibot.json")
    workspace = WorkspaceManager(root, config.workspace_dir)
    workspace.ensure()
    return EvidenceStore(workspace.evidence_dir)


def _run_tasks(args: argparse.Namespace, root: Path | None = None) -> int:
    store = _load_task_store(root)
    command = args.tasks_command
    if command is None:
        print("tasks_command_missing", file=sys.stderr)
        return 1

    if command == "create":
        record = store.create(
            goal=args.goal,
            session_id=getattr(args, "session", None),
            user_id=getattr(args, "user", None),
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if command == "list":
        result = store.list()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if command == "show":
        record = store.get(args.task_id)
        if record is None:
            print(f"task_not_found: {args.task_id}", file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if command == "cancel":
        try:
            record = store.cancel(args.task_id)
        except TaskStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if command == "resume":
        return _task_resume(store, args.task_id, root=root)

    print(f"unknown_tasks_command: {command}", file=sys.stderr)
    return 1


def _task_resume(store: TaskStore, task_id: str, root: Path | None = None) -> int:
    """Run *task_id* through AgentLoop and update task state from the run result."""

    task = store.get(task_id)
    if task is None:
        print(f"task_not_found: {task_id}", file=sys.stderr)
        return 1

    if task.get("status") == "cancelled":
        print(json.dumps({"error": "task_is_cancelled", "task_id": task_id}, ensure_ascii=False), file=sys.stderr)
        return 1

    # Mark running before dispatch
    store.update(task_id, status="running")

    # Build the harness message
    message = ChannelMessage(
        channel="cli",
        user_id=str(task.get("user_id") or "local-user"),
        session_id=str(task.get("session_id") or str(uuid4())),
        content=str(task["goal"]),
        metadata={"task_id": task_id},
    )

    # Execute through the shared AgentLoop
    try:
        app = MiniBotApp(root)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        store.update(task_id, status="failed", stop_reason=str(exc))
        return 1

    result = app.runtime.agent_loop.handle_message(message)

    # Read the full run record for decision-making
    run_path = app.runtime.agent_loop.recorder.runs_dir / f"{result.run_id}.json"
    try:
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        store.update(task_id, status="failed", last_run_id=result.run_id, stop_reason="run_record_read_error")
        _print_task(store, task_id)
        return 0

    stop_reason = run_record.get("stop_reason")
    failure_category = run_record.get("failure_category")
    tool_results = run_record.get("tool_results", [])

    # --- Determine final task status from run evidence ---

    # approval_required
    approval_entry = next((r for r in tool_results if r.get("status") == "approval_required"), None)
    if approval_entry is not None:
        meta = dict(approval_entry.get("metadata", {}))
        approval_id = meta.get("approval_id")
        store.update(
            task_id,
            status="waiting_approval",
            last_run_id=result.run_id,
            pending_approval_id=approval_id,
            stop_reason="approval_required",
        )
        _print_task(store, task_id)
        return 0

    # approval_rejected
    if any(r.get("failure_category") == "approval_rejected" for r in tool_results):
        store.update(
            task_id,
            status="failed",
            last_run_id=result.run_id,
            stop_reason="approval_rejected",
        )
        _print_task(store, task_id)
        return 0

    # budget / loop stop reasons
    if stop_reason in {
        "max_tool_rounds_reached",
        "max_tool_calls_reached",
        "max_runtime_reached",
        "duplicate_loop_detected",
    }:
        store.update(
            task_id,
            status="failed",
            last_run_id=result.run_id,
            stop_reason=stop_reason,
        )
        _print_task(store, task_id)
        return 0

    # blocked_by_policy
    if failure_category == "blocked_by_policy":
        store.update(
            task_id,
            status="failed",
            last_run_id=result.run_id,
            stop_reason="blocked_by_policy",
        )
        _print_task(store, task_id)
        return 0

    # other failure categories
    if failure_category and failure_category != "approval_required":
        store.update(
            task_id,
            status="failed",
            last_run_id=result.run_id,
            stop_reason=str(failure_category),
        )
        _print_task(store, task_id)
        return 0

    # success
    store.update(
        task_id,
        status="completed",
        last_run_id=result.run_id,
        stop_reason=str(stop_reason),
    )
    _print_task(store, task_id)
    return 0


def _print_task(store: TaskStore, task_id: str) -> None:
    """Print the latest task record as JSON."""
    record = store.get(task_id)
    print(json.dumps(record, ensure_ascii=False, indent=2))


def _load_task_store(root_override: Path | None = None) -> TaskStore:
    root = (root_override or Path.cwd()).resolve()
    config = load_config(root / "configs" / "minibot.json")
    workspace = WorkspaceManager(root, config.workspace_dir)
    workspace.ensure()
    tasks_dir = root / config.workspace_dir / "tasks"
    return TaskStore(tasks_dir)


def _configure_stdio() -> None:
    """Force UTF-8 compatible stdio on runtimes that support reconfigure()."""

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _resolve_event_path(args: argparse.Namespace) -> Path:
    """Support both positional and flag-based Feishu mock event paths."""

    event_value = args.event_path or args.event
    return Path(event_value)


def _load_approval_store() -> ApprovalStore:
    root = Path.cwd().resolve()
    config = load_config(root / "configs" / "minibot.json")
    workspace = WorkspaceManager(root, config.workspace_dir)
    workspace.ensure()
    return ApprovalStore(workspace.approvals_dir)


def _build_benchmark_app(mode: str) -> MiniBotApp:
    if mode == "real":
        try:
            with _temporary_model_mode("real"):
                return MiniBotApp()
        except RuntimeError as exc:
            if "deepseek_config_missing" not in str(exc):
                raise
            with _temporary_model_mode("fake"):
                return MiniBotApp()
    with _temporary_model_mode("fake"):
        return MiniBotApp()


@contextmanager
def _temporary_model_mode(mode: str):
    previous = os.environ.get("MINIBOT_MODEL_MODE")
    os.environ["MINIBOT_MODEL_MODE"] = mode
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("MINIBOT_MODEL_MODE", None)
        else:
            os.environ["MINIBOT_MODEL_MODE"] = previous
