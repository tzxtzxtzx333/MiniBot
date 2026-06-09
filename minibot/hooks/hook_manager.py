"""Structured Hook runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from minibot.json_utils import JsonFileError, load_json_file

from .actions import HookActionRegistry, HookResult
from .matchers import ExactMatcher, RegexMatcher


class HookManager:
    """Load hooks, match events, invoke registered actions, and return structured results."""

    def __init__(
        self, config_path: Path, approval_prompt: Callable[[str], bool] | None = None
    ) -> None:
        self.config_path = config_path
        self.approval_prompt = approval_prompt
        self.config = self._load_config()
        self.matchers = {
            "exact": ExactMatcher(),
            "regex": RegexMatcher(),
        }

    @property
    def hooks(self) -> list[dict[str, object]]:
        return list(self.config.get("hooks", []))

    @property
    def defaults(self) -> dict[str, object]:
        return dict(self.config.get("defaults", {}))

    def trigger(
        self, event: str, match_value: str, context: dict[str, object] | None = None
    ) -> list[HookResult]:
        """Evaluate hooks for one event and return structured execution results."""

        runtime_context = dict(context or {})
        runtime_context["event"] = event
        runtime_context["match_value"] = match_value
        runtime_context["default_auto_approve"] = self.defaults.get("auto_approve", False)

        results: list[HookResult] = []
        for hook in self.hooks:
            if hook.get("event") != event:
                continue
            matcher = self.matchers[hook["match_type"]]
            if not matcher.matches(str(hook["pattern"]), match_value):
                continue
            try:
                action = HookActionRegistry.create(
                    str(hook["action"]),
                    hook_config=hook,
                    approval_prompt=self.approval_prompt,
                )
                result = action.execute(runtime_context)
            except Exception as exc:  # noqa: BLE001
                result = {
                    "hook_name": hook["name"],
                    "event": event,
                    "action": hook["action"],
                    "status": "error",
                    "matched": True,
                    "blocked": False,
                    "error": str(exc),
                    "redacted_fields": [],
                    "tags": [],
                    "message": "hook_execution_failed",
                }
            results.append(result)
        return results

    def _load_config(self) -> dict[str, object]:
        if not self.config_path.exists():
            return {"defaults": {"auto_approve": False}, "hooks": []}
        try:
            return load_json_file(self.config_path)
        except JsonFileError as exc:
            raise ValueError(f"Invalid hook config: {self.config_path} ({exc})") from exc
