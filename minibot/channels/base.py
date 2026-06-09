"""Channel abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ChannelMessage:
    """Normalized message passed from a channel into the harness."""

    channel: str
    user_id: str
    session_id: str
    content: str
    metadata: dict[str, object] = field(default_factory=dict)


class BaseChannel:
    """Base type for all channel adapters."""

    channel_name = "base"

    def __init__(self, agent_loop, long_task_runner=None, planner_agent=None) -> None:
        self.agent_loop = agent_loop
        self.long_task_runner = long_task_runner
        self.planner_agent = planner_agent

    def dispatch_message(self, message: ChannelMessage):
        """Forward a normalized message to the shared AgentLoop."""

        return self.agent_loop.handle_message(message)

    def dispatch_plan(self, message: ChannelMessage):
        """Detect /plan prefix and route to PlannerAgent + LongTaskRunner."""
        if self.long_task_runner is None or self.planner_agent is None:
            return None

        content = message.content.strip()
        if not content.startswith("/plan"):
            return None

        goal = content[len("/plan") :].strip()
        if not goal:
            goal = content  # whole message is the goal

        plan = self.planner_agent.plan(goal, task_id=str(message.metadata.get("task_id", "")))
        result = self.long_task_runner.run(plan, session_id=message.session_id)

        if result["status"] == "waiting_approval":
            # Find approval_id from step_outcomes
            approval_id = ""
            for o in result.get("step_outcomes", []):
                aid = o.get("pending_approval_id")
                if aid:
                    approval_id = str(aid)
                    break
            return (
                f"任务等待审批：\n"
                f"plan_id: {result['plan_id']}\n"
                f"approval_id: {approval_id}\n"
                f"请通过 CLI 或 HTTP Approval API 批准后执行 plan resume {result['plan_id']}"
            )

        evidence_count = sum(
            len(o.get("evidence_ids", [])) for o in result.get("step_outcomes", [])
        )
        return (
            f"任务已完成：\n"
            f"- plan_id: {result['plan_id']}\n"
            f"- status: {result['status']}\n"
            f"- steps: {result.get('total_steps', 0)}\n"
            f"- evidence_count: {evidence_count}"
        )
