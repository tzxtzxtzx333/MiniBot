"""Hook actions and action registry."""

from __future__ import annotations

import re
import sys
from typing import Callable

HookResult = dict[str, object]


class HookAction:
    """Base class for hook actions."""

    def __init__(
        self, hook_config: dict[str, object], approval_prompt: Callable[[str], bool] | None = None
    ) -> None:
        self.hook_config = hook_config
        self.approval_prompt = approval_prompt

    def execute(self, context: dict[str, object]) -> HookResult:
        raise NotImplementedError

    def _base_result(self, context: dict[str, object]) -> HookResult:
        return {
            "hook_name": self.hook_config["name"],
            "event": context["event"],
            "action": self.hook_config["action"],
            "status": "applied",
            "matched": True,
            "blocked": False,
            "error": None,
            "redacted_fields": [],
            "tags": [],
            "message": "",
        }


class LogAction(HookAction):
    """Attach a log-style note to the hook results."""

    def execute(self, context: dict[str, object]) -> HookResult:
        result = self._base_result(context)
        result["message"] = str(self.hook_config.get("message", f"log:{context['event']}"))
        return result


class ApprovalAction(HookAction):
    """Perform a simple approval gate."""

    def execute(self, context: dict[str, object]) -> HookResult:
        result = self._base_result(context)
        auto_approve = self.hook_config.get("auto_approve")
        if auto_approve is None:
            auto_approve = bool(context.get("default_auto_approve", False))

        if (
            self._can_prompt()
            and self.approval_prompt is not None
            and self.hook_config.get("interactive", True)
        ):
            approved = self.approval_prompt(
                str(
                    self.hook_config.get(
                        "message", f"Approve hook {self.hook_config['name']}? [y/N] "
                    )
                )
            )
        else:
            approved = bool(auto_approve)

        result["message"] = "approved" if approved else "approval_denied"
        if not approved:
            result["status"] = "denied"
            result["blocked"] = True
        return result

    def _can_prompt(self) -> bool:
        return bool(getattr(sys.stdin, "isatty", lambda: False)()) and bool(
            getattr(sys.stdout, "isatty", lambda: False)()
        )


class BlockAction(HookAction):
    """Block the current operation."""

    def execute(self, context: dict[str, object]) -> HookResult:
        result = self._base_result(context)
        result["status"] = "blocked"
        result["blocked"] = True
        result["message"] = str(self.hook_config.get("message", "blocked_by_hook"))
        return result


class RedactAction(HookAction):
    """Redact configured content fields."""

    def execute(self, context: dict[str, object]) -> HookResult:
        result = self._base_result(context)
        target_field = str(self.hook_config.get("target_field", "value"))
        replacement = str(self.hook_config.get("replacement", "[REDACTED]"))
        pattern = str(self.hook_config.get("pattern", ""))
        value = str(context.get(target_field, ""))
        updated_value, count = re.subn(pattern, replacement, value)
        result["message"] = "redacted" if count else "no_redaction_applied"
        if count:
            result["redacted_fields"] = [target_field]
            result["updated_value"] = updated_value
        return result


class TagAction(HookAction):
    """Attach structured tags to the current hook context."""

    def execute(self, context: dict[str, object]) -> HookResult:
        result = self._base_result(context)
        tags = self.hook_config.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        resolved_tags = list(tags) if tags else [f"hook:{self.hook_config['name']}"]
        result["tags"] = resolved_tags
        result["message"] = "tagged"
        return result


class HookActionRegistry:
    """Registry for decoupled hook actions."""

    _actions: dict[str, type[HookAction]] = {}

    @classmethod
    def register(cls, name: str, action_cls: type[HookAction]) -> None:
        cls._actions[name] = action_cls

    @classmethod
    def create(
        cls,
        name: str,
        hook_config: dict[str, object],
        approval_prompt: Callable[[str], bool] | None = None,
    ) -> HookAction:
        return cls._actions[name](hook_config=hook_config, approval_prompt=approval_prompt)


HookActionRegistry.register("log", LogAction)
HookActionRegistry.register("require_approval", ApprovalAction)
HookActionRegistry.register("block", BlockAction)
HookActionRegistry.register("redact", RedactAction)
HookActionRegistry.register("tag", TagAction)
