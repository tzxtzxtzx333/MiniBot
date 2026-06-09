"""TaskExecutor — execute one plan step through the existing AgentLoop."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from minibot.channels.base import ChannelMessage

from .plan_schema import Step, TaskPlan

# Match filenames like README.md, docs/resume_mapping.md, output.txt
_FILE_PATTERN = re.compile(r"[\w./-]+\.(?:md|txt|json|py|yaml|yml|toml|cfg|ini|log|csv)")


class TaskExecutor:
    """Execute a single plan step by wrapping it as a ChannelMessage and
    delegating to the existing AgentLoop.

    Before execution, files referenced in ``file_read`` steps are
    automatically copied from the project root into the sandbox when
    they are missing.
    """

    def __init__(
        self,
        agent_loop,
        step_verifier,
        plan_store_dir: Path,
        workspace=None,
        *,
        lean_context: bool = True,
    ) -> None:
        self._agent_loop = agent_loop
        self._step_verifier = step_verifier
        self._plan_dir = plan_store_dir
        self._workspace = workspace
        self.lean_context = lean_context
        self._plan_dir.mkdir(parents=True, exist_ok=True)

    def execute_step(
        self,
        plan: TaskPlan,
        step: Step,
        session_id: str = "",
        accumulated_context: str = "",
    ) -> dict[str, object]:
        """Execute *step* through AgentLoop and return an outcome dict."""
        # Pre-flight: ensure files referenced in the step exist in sandbox
        if "file_read" in step.tool_hints:
            self._ensure_files_in_sandbox(step)

        # Build message content with prior step results injected (capped)
        content = step.description
        if accumulated_context.strip():
            capped = accumulated_context
            if len(capped) > 1500:
                capped = capped[:1500] + "\n…[truncated]"
            content = (
                "以下是之前步骤的执行结果，请基于这些信息完成当前任务：\n\n"
                f"{capped}\n\n"
                "---\n\n"
                f"当前任务：{step.description}"
            )

        # ── Lean context mode ──
        # When enabled, plan steps skip HISTORY / MEMORY / Archives to avoid
        # injecting irrelevant external context.  Step-to-step continuity is
        # preserved via accumulated_context.  Set lean_context=False to keep
        # full context (e.g. when a plan depends on prior conversation).
        cb = self._agent_loop.context_builder
        if self.lean_context:
            saved = (cb.enable_history_retrieval, cb.enable_history_truncation,
                     cb.enable_memory_compaction, cb.enable_archive_recall)
            cb.enable_history_retrieval = False
            cb.enable_history_truncation = False
            cb.enable_memory_compaction = False
            cb.enable_archive_recall = False
            try:
                result = self._dispatch(plan, step, content, session_id)
            finally:
                (cb.enable_history_retrieval, cb.enable_history_truncation,
                 cb.enable_memory_compaction, cb.enable_archive_recall) = saved
        else:
            result = self._dispatch(plan, step, content, session_id)

        return result

    def _dispatch(
        self,
        plan: TaskPlan,
        step: Step,
        content: str,
        session_id: str,
    ) -> dict[str, object]:
        message = ChannelMessage(
            channel="plan-executor",
            user_id="planner",
            session_id=session_id or f"plan-{plan.plan_id}",
            content=content,
            metadata={
                "task_id": plan.task_id,
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
            },
        )

        result = self._agent_loop.handle_message(message)
        run_id = result.run_id

        runs_dir = self._agent_loop.recorder.runs_dir
        run_path = runs_dir / f"{run_id}.json"
        try:
            run_record = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run_record = {}

        step.run_id = run_id
        evidence_ids = list(run_record.get("evidence_ids", []))
        step.evidence_ids = evidence_ids

        verdict = self._step_verifier.verify(step, run_record)
        step.status = verdict["status"]
        step.failure_category = verdict.get("failure_category")

        pending_approval_id = None
        if verdict["status"] == "waiting_approval":
            for tr in run_record.get("tool_results", []):
                meta = dict(tr.get("metadata", {}))
                if meta.get("approval_id"):
                    pending_approval_id = str(meta["approval_id"])
                    break

        self._save_plan(plan)

        return {
            "status": verdict["status"],
            "reason": verdict.get("reason", ""),
            "run_id": run_id,
            "evidence_ids": evidence_ids,
            "tool_trace": run_record.get("tool_trace", []),
            "final_response": str(run_record.get("final_response", "")),
            "failure_category": verdict.get("failure_category"),
            "pending_approval_id": pending_approval_id,
        }

    # ------------------------------------------------------------------
    # Sandbox pre-flight
    # ------------------------------------------------------------------

    def _ensure_files_in_sandbox(self, step: Step) -> None:
        """Copy files referenced in *step* from project root to sandbox."""
        if self._workspace is None:
            return
        sandbox = self._workspace.sandbox_dir
        project_root = self._workspace.project_root

        for match in _FILE_PATTERN.finditer(step.description):
            rel_path = match.group(0)
            sandbox_path = sandbox / rel_path
            if sandbox_path.exists():
                continue
            source = project_root / rel_path
            if source.is_file():
                sandbox_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, sandbox_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Plan persistence
    # ------------------------------------------------------------------

    def _plan_path(self, plan_id: str) -> Path:
        return self._plan_dir / f"{plan_id}.json"

    def load_plan(self, plan_id: str) -> TaskPlan | None:
        path = self._plan_path(plan_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return TaskPlan.from_dict(data)

    def save_plan(self, plan: TaskPlan) -> None:
        self._save_plan(plan)

    def _save_plan(self, plan: TaskPlan) -> None:
        from datetime import datetime, timezone

        plan.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._plan_path(plan.plan_id)
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
