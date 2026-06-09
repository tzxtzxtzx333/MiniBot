"""LongTaskRunner — execute all steps of a TaskPlan sequentially."""

from __future__ import annotations

from .plan_schema import TaskPlan, Step
from .task_executor import TaskExecutor
from .replanner_agent import ReplannerAgent


class LongTaskRunner:
    """Orchestrate sequential execution of a TaskPlan through AgentLoop.

    Handles step execution, approval pausing, failure re-planning, and
    final status synthesis.
    """

    def __init__(
        self,
        task_executor: TaskExecutor,
        replanner: ReplannerAgent,
        task_store=None,
    ) -> None:
        self._executor = task_executor
        self._replanner = replanner
        self._task_store = task_store

    def run(self, plan: TaskPlan, session_id: str = "") -> dict[str, object]:
        """Execute all pending steps of *plan* sequentially.

        Returns a result dict with ``plan_id``, ``status``,
        ``steps_completed``, ``steps_failed``, and ``step_outcomes``.
        """
        if plan.status in {"completed", "failed"}:
            # Reset and re-run from the beginning
            for step in plan.steps:
                step.status = "pending"
                step.run_id = None
                step.evidence_ids = []
                step.failure_category = None
                step.retry_count = 0
            plan.status = "pending"
            plan.metadata.pop("replan_events", None)

        plan.status = "running"
        self._executor.save_plan(plan)
        if self._task_store:
            self._update_task(plan, "running")

        outcomes: list[dict[str, object]] = []
        steps_completed = 0
        steps_failed = 0
        stopped = False
        stop_reason: str | None = None

        idx = plan.current_step_index()
        while idx is not None and not stopped:
            step = plan.steps[idx]

            # Skip already-completed steps
            if step.status == "completed":
                idx += 1
                if idx >= len(plan.steps):
                    break
                continue

            step.status = "running"
            self._executor.save_plan(plan)

            # Build context from prior completed steps
            accumulated = self._build_accumulated_context(plan, outcomes)

            outcome = self._executor.execute_step(plan, step, session_id=session_id, accumulated_context=accumulated)
            outcomes.append(outcome)

            if outcome["status"] == "completed":
                steps_completed += 1
                idx = plan.current_step_index()

            elif outcome["status"] == "waiting_approval":
                plan.status = "waiting_approval"
                self._executor.save_plan(plan)
                if self._task_store:
                    self._update_task(plan, "waiting_approval",
                                      pending_approval_id=str(outcome.get("pending_approval_id") or ""))
                stop_reason = "waiting_approval"
                stopped = True

            elif outcome["status"] == "failed":
                steps_failed += 1
                fc = outcome.get("failure_category")

                # Try replan
                if self._replanner.should_replan(step, fc):
                    replacement = self._replanner.replan(plan, step, str(outcome.get("reason", "")))
                    if replacement is not None:
                        # Insert replacement after the failed step
                        plan.steps.insert(idx + 1, replacement)
                        # Mark original as skipped so current_step_index() finds the replacement
                        plan.steps[idx].status = "skipped"
                        self._executor.save_plan(plan)
                        idx = plan.current_step_index()
                        continue

                # Cannot replan → plan failed
                plan.status = "failed"
                self._executor.save_plan(plan)
                if self._task_store:
                    self._update_task(plan, "failed", stop_reason=str(outcome.get("reason", "")))
                stop_reason = f"step_{step.step_id}_failed"
                stopped = True
            else:
                idx = plan.current_step_index()

        # Check if all completed
        if not stopped and plan.all_completed():
            plan.status = "completed"
            self._executor.save_plan(plan)
            if self._task_store:
                self._update_task(plan, "completed")

        return self._result(plan, outcomes)

    def resume(self, plan_id: str, session_id: str = "") -> dict[str, object]:
        """Resume a paused plan (e.g. after approval)."""
        plan = self._executor.load_plan(plan_id)
        if plan is None:
            return {
                "plan_id": plan_id,
                "status": "error",
                "error": "plan_not_found",
                "steps_completed": 0,
                "steps_failed": 0,
                "step_outcomes": [],
            }
        if plan.status == "completed":
            return self._result(plan, [])

        # Check approvals and resolve waiting steps
        for step in plan.steps:
            if step.status != "waiting_approval":
                continue
            resolution = self._check_approval_resolution(step)
            if resolution == "approved":
                # Replay the exact approved tool call so the file is actually written
                approved_args = self._get_approved_arguments(step)
                if approved_args is not None:
                    self._execute_approved_tool(step, approved_args)
                    step.status = "completed"
                else:
                    step.status = "completed"
            elif resolution == "rejected":
                step.status = "failed"
                step.failure_category = "approval_rejected"
            # still "pending" → leave as waiting_approval

        if plan.all_completed():
            plan.status = "completed"
            self._executor.save_plan(plan)
        else:
            plan.status = "running"
            self._executor.save_plan(plan)
            return self.run(plan, session_id=session_id)
        return {
            "plan_id": plan.plan_id,
            "task_id": plan.task_id,
            "status": plan.status,
            "goal": plan.goal,
            "total_steps": len(plan.steps),
            "steps_completed": len(plan.steps),
            "steps_failed": 0,
            "step_outcomes": [],
        }

    def _get_approved_arguments(self, step: Step) -> dict[str, object] | None:
        """Read the approved tool arguments from the step's run record."""
        if step.run_id is None:
            return None
        runs_dir = self._executor._agent_loop.recorder.runs_dir
        run_path = runs_dir / f"{step.run_id}.json"
        try:
            import json
            record = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        for tr in record.get("tool_trace", []):
            meta = dict(tr.get("metadata", {}))
            if meta.get("approval_id"):
                return {"tool_name": str(tr.get("tool_name", "")), "arguments": dict(tr.get("arguments", {}))}
        return None

    def _execute_approved_tool(self, step: Step, approved_args: dict[str, object]) -> None:
        """Dispatch an approved tool call through the existing ToolDispatcher."""
        tool_name = str(approved_args.get("tool_name", ""))
        arguments = dict(approved_args.get("arguments", {}))
        dispatcher = self._executor._agent_loop.tool_dispatcher
        results, trace = dispatcher.dispatch(
            [{"tool_name": tool_name, "arguments": arguments}],
            dispatch_context={"user_id": "planner", "session_id": f"plan-resume-{step.step_id}"},
        )
        # Record the replay as a new run for audit
        if results:
            step.evidence_ids = list(step.evidence_ids or [])

    def _check_approval_resolution(self, step: Step) -> str | None:
        """Check the approval store for the step's pending approval.

        Returns ``"approved"``, ``"rejected"``, or ``None`` (still pending).
        """
        if step.run_id is None:
            return None
        runs_dir = self._executor._agent_loop.recorder.runs_dir
        run_path = runs_dir / f"{step.run_id}.json"
        try:
            import json
            record = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        # Find the approval_id from tool_trace (has arguments for matching)
        for tr in record.get("tool_trace", []):
            meta = dict(tr.get("metadata", {}))
            approval_id = meta.get("approval_id")
            if not approval_id:
                continue
            # Check if resolved via the approval store (use the active store, not a new one)
            try:
                store = self._executor._agent_loop.tool_dispatcher.approval_store
                resolution = store.find_resolution(
                    user_id="planner",
                    tool_name=str(tr.get("tool_name", "")),
                    arguments=dict(tr.get("arguments", {})),
                )
                if resolution is not None:
                    status = str(resolution.get("status", ""))
                    if status == "approved":
                        return "approved"
                    if status == "rejected":
                        return "rejected"
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_accumulated_context(plan: TaskPlan, outcomes: list[dict[str, object]]) -> str:
        """Collect final_response from completed prior steps."""
        parts: list[str] = []
        for outcome in outcomes:
            if outcome.get("status") != "completed":
                continue
            final = str(outcome.get("final_response", "")).strip()
            if final:
                # Truncate very long responses to keep context manageable
                if len(final) > 800:
                    final = final[:800] + "\n…[truncated]"
                parts.append(f"[步骤结果]\n{final}")
        return "\n\n".join(parts)

    @staticmethod
    def _result(plan: TaskPlan, outcomes: list[dict[str, object]]) -> dict[str, object]:
        return {
            "plan_id": plan.plan_id,
            "task_id": plan.task_id,
            "status": plan.status,
            "goal": plan.goal,
            "total_steps": len(plan.steps),
            "steps_completed": sum(1 for o in outcomes if o.get("status") == "completed"),
            "steps_failed": sum(1 for o in outcomes if o.get("status") == "failed"),
            "step_outcomes": outcomes,
        }

    def _update_task(self, plan: TaskPlan, status: str, **extra: object) -> None:
        if self._task_store is None or plan.task_id is None:
            return
        try:
            self._task_store.update(plan.task_id, status=status, **extra)
        except Exception:
            pass
