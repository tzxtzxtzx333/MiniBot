"""ReplannerAgent — minimal failure re-planning for failed steps."""

from __future__ import annotations

from .plan_schema import Step, TaskPlan


class ReplannerAgent:
    """Generate replacement steps when a step fails.

    Only handles non-blocking failures (i.e. NOT approval_required or
    blocked_by_policy).  Each step is re-planned at most once.
    """

    # Failure categories that should NOT be re-planned
    _NO_REPLAN_CATEGORIES = frozenset({
        "approval_required",
        "approval_rejected",
        "blocked_by_policy",
    })

    def __init__(self, *, mode: str = "fake") -> None:
        self.mode = mode

    def should_replan(self, step: Step, failure_category: str | None) -> bool:
        """Return True if the step qualifies for re-planning."""
        if failure_category and failure_category in self._NO_REPLAN_CATEGORIES:
            return False
        if step.retry_count >= 1:
            return False
        return True

    def replan(self, plan: TaskPlan, failed_step: Step, failure_reason: str) -> Step | None:
        """Create a replacement step for *failed_step*.

        When a ``file_read`` step fails with ``file_not_found``, the replacement
        falls back to reading ``README.md`` instead of retrying the missing file.
        """
        fc = failed_step.failure_category
        if fc and fc in self._NO_REPLAN_CATEGORIES:
            return None
        if failed_step.retry_count >= 1:
            return None

        # Mark the original step as exhausted
        failed_step.retry_count += 1

        # Generate a smart replacement
        new_description = failed_step.description
        new_tool_hints = list(failed_step.tool_hints)
        strategy = "retry"

        if fc == "file_not_found" and "file_read" in failed_step.tool_hints:
            new_description = f"读取 README.md（原文件 {failed_step.description} 不存在，改用 README.md）"
            new_tool_hints = ["file_read"]
            strategy = "fallback_to_readme"

        replacement = Step(
            step_id=f"{failed_step.step_id}_r1",
            description=new_description,
            expected_output=failed_step.expected_output,
            status="pending",
            tool_hints=new_tool_hints,
            depends_on=list(failed_step.depends_on),
            retry_count=failed_step.retry_count,
        )

        # Record replan event in plan metadata
        from datetime import datetime, timezone
        replan_events = plan.metadata.get("replan_events", [])
        if not isinstance(replan_events, list):
            replan_events = []
        replan_events.append(
            {
                "original_step_id": failed_step.step_id,
                "new_step_id": replacement.step_id,
                "reason": fc or "unknown",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "strategy": strategy,
            }
        )
        plan.metadata["replan_events"] = replan_events

        return replacement
