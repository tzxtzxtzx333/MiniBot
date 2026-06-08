"""Status checks for the MiniBot CLI."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import AgentBudgetProfile, MiniBotConfig
from .workspace import WorkspaceManager


@dataclass(slots=True)
class StatusReport:
    """Structured status snapshot for CLI rendering and tests."""

    version: str
    config_files: dict[str, bool]
    workspace_exists: bool
    docker_available: bool
    benchmark_case_count: int
    benchmark_case_count_by_profile: dict[str, int]
    benchmark_case_count_by_category: dict[str, int]
    memory_exists: bool
    history_exists: bool
    archives_dir_exists: bool
    archive_count: int
    latest_archive_path: str | None
    feishu_config_present: bool
    reports_dir_exists: bool
    tasks_dir_exists: bool = False
    task_count: int = 0
    pending_task_count: int = 0
    approval_pending_count: int = 0
    budget: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize status to JSON."""

        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


class MiniBotStatusService:
    """Aggregate system checks required by the PRD."""

    def __init__(
        self,
        project_root: Path,
        config: MiniBotConfig,
        workspace: WorkspaceManager,
        task_store=None,
        approval_store=None,
    ) -> None:
        self.project_root = project_root
        self.config = config
        self.workspace = workspace
        self._task_store = task_store
        self._approval_store = approval_store

    def collect(self) -> StatusReport:
        """Build the current status report."""

        benchmark_summary = _benchmark_case_summary(self.project_root / "benchmarks")
        config_files = {
            name: (self.project_root / "configs" / name).exists()
            for name in ["minibot.json", "hooks.json", "tools.json", "policy.json"]
        }
        archive_info = _safe_archive_info(self.workspace.archives_dir)
        tasks_info = _safe_tasks_info(self._task_store)
        approval_counts = _safe_approval_counts(self._approval_store)
        budget = {
            "agent_profile": os.environ.get("MINIBOT_AGENT_PROFILE", "default"),
            "max_tool_rounds": self.config.budget.max_tool_rounds,
            "max_tool_calls_total": self.config.budget.max_tool_calls_total,
            "max_runtime_seconds": self.config.budget.max_runtime_seconds,
            "max_same_tool_calls": self.config.budget.max_same_tool_calls,
        }
        return StatusReport(
            version=self.config.version,
            config_files=config_files,
            workspace_exists=self.workspace.root.exists(),
            docker_available=_docker_available(),
            benchmark_case_count=benchmark_summary["benchmark_case_count"],
            benchmark_case_count_by_profile=benchmark_summary["benchmark_case_count_by_profile"],
            benchmark_case_count_by_category=benchmark_summary["benchmark_case_count_by_category"],
            memory_exists=self.workspace.memory_file.exists(),
            history_exists=self.workspace.history_file.exists(),
            archives_dir_exists=archive_info["archives_dir_exists"],
            archive_count=archive_info["archive_count"],
            latest_archive_path=archive_info["latest_archive_path"],
            feishu_config_present=bool(os.getenv("FEISHU_APP_ID")) and bool(os.getenv("FEISHU_APP_SECRET")),
            reports_dir_exists=(self.project_root / "reports").exists(),
            tasks_dir_exists=tasks_info["tasks_dir_exists"],
            task_count=tasks_info["task_count"],
            pending_task_count=tasks_info["pending_task_count"],
            approval_pending_count=approval_counts["pending_count"],
            budget=budget,
        )


def _benchmark_case_summary(benchmarks_root: Path) -> dict[str, object]:
    summary = {
        "benchmark_case_count": 0,
        "benchmark_case_count_by_profile": {},
        "benchmark_case_count_by_category": {},
    }
    for path in benchmarks_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        summary["benchmark_case_count"] += 1
        category = str(payload.get("category", "unknown"))
        by_category = summary["benchmark_case_count_by_category"]
        by_category[category] = by_category.get(category, 0) + 1
        profiles = payload.get("profiles", [])
        if isinstance(profiles, list) and profiles:
            for profile in profiles:
                profile_name = str(profile)
                by_profile = summary["benchmark_case_count_by_profile"]
                by_profile[profile_name] = by_profile.get(profile_name, 0) + 1
        else:
            by_profile = summary["benchmark_case_count_by_profile"]
            by_profile["default"] = by_profile.get("default", 0) + 1
    summary["benchmark_case_count_by_profile"] = dict(sorted(summary["benchmark_case_count_by_profile"].items()))
    summary["benchmark_case_count_by_category"] = dict(sorted(summary["benchmark_case_count_by_category"].items()))
    return summary


def _safe_archive_info(archives_dir: Path) -> dict[str, object]:
    """Return archive count / latest path without crashing on bad files."""
    if not archives_dir.is_dir():
        return {
            "archives_dir_exists": False,
            "archive_count": 0,
            "latest_archive_path": None,
        }
    archive_paths: list[Path] = []
    try:
        for entry in archives_dir.iterdir():
            if entry.is_file() and entry.suffix == ".md":
                archive_paths.append(entry)
    except OSError:
        pass
    archive_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = str(archive_paths[0].name) if archive_paths else None
    return {
        "archives_dir_exists": True,
        "archive_count": len(archive_paths),
        "latest_archive_path": latest,
    }


def _safe_tasks_info(task_store) -> dict[str, object]:
    """Return task counts without crashing on missing or corrupt store."""
    if task_store is None:
        return {"tasks_dir_exists": False, "task_count": 0, "pending_task_count": 0}
    try:
        tasks_dir = task_store.root
        all_tasks = task_store.list(limit=10000)
        pending = [t for t in all_tasks if t.get("status") == "pending"]
        return {
            "tasks_dir_exists": tasks_dir.exists(),
            "task_count": len(all_tasks),
            "pending_task_count": len(pending),
        }
    except Exception:
        return {"tasks_dir_exists": False, "task_count": 0, "pending_task_count": 0}


def _safe_approval_counts(approval_store) -> dict[str, int]:
    """Return approval counts without crashing on missing or corrupt store."""
    if approval_store is None:
        return {"pending_count": 0}
    try:
        return approval_store.counts()
    except Exception:
        return {"pending_count": 0}


def _docker_available() -> bool:
    """Return whether the local Docker CLI looks usable."""

    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())
