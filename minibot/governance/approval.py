"""Approval decisions for graylisted or high-risk tool usage."""

from __future__ import annotations

from dataclasses import dataclass
import sys


@dataclass(slots=True)
class ApprovalDecision:
    """Structured approval outcome."""

    approved: bool
    reason: str
    requires_approval: bool


class ApprovalManager:
    """Resolve whether a tool call is approved under the active policy."""

    def __init__(self, policy: dict[str, object]) -> None:
        self.policy = policy
        self.approval_config = dict(policy.get("approval", {}))
        self.tool_defaults = dict(self.approval_config.get("tool_defaults", {}))

    def decide(self, tool_name: str, *, requires_approval: bool) -> ApprovalDecision:
        """Return an approval decision for one tool call."""

        if not requires_approval:
            return ApprovalDecision(approved=True, reason="auto_allowed", requires_approval=False)

        global_default = self.approval_config.get("auto_approve")
        if isinstance(global_default, bool):
            if global_default:
                return ApprovalDecision(approved=True, reason="auto_approved", requires_approval=True)

        override = self.tool_defaults.get(tool_name)
        if isinstance(override, bool):
            return ApprovalDecision(approved=False, reason="approval_required", requires_approval=True)

        if self._can_prompt():
            approved = self._prompt(tool_name)
            return ApprovalDecision(
                approved=approved,
                reason="cli_approved" if approved else "approval_required",
                requires_approval=True,
            )

        return ApprovalDecision(approved=False, reason="approval_required", requires_approval=True)

    @staticmethod
    def _can_prompt() -> bool:
        try:
            return bool(sys.stdin and sys.stdin.isatty())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _prompt(tool_name: str) -> bool:
        try:
            answer = input(f"Approve tool '{tool_name}'? [y/N]: ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}
