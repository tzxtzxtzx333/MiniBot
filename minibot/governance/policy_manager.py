"""Tool policy loading and validation."""

from __future__ import annotations

from pathlib import Path

from minibot.json_utils import load_json_file
from minibot.tools.base import ToolError, ToolSpec


class ToolPolicyManager:
    """Validate tool execution against governance policy."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        raw = dict(load_json_file(project_root / "configs" / "policy.json"))
        self.policy = self._normalize_policy(raw)

    def validate(
        self, tool_name: str, payload: dict[str, object], spec: ToolSpec | None = None
    ) -> None:
        """Raise a structured error when a tool call violates policy."""

        if tool_name in self.policy["blacklist"]:
            raise ToolError("blocked_by_policy", "blocked_by_policy")
        self._validate_shell_blacklist(payload)
        self._validate_parameter_limits(tool_name, payload)
        if (
            spec is not None
            and spec.sandbox_required
            and tool_name not in {"python_exec", "shell_exec"}
        ):
            raise ToolError("sandbox_policy_misconfigured", "blocked_by_policy")

    def requires_approval(self, tool_name: str, spec: ToolSpec | None = None) -> bool:
        """Return whether this tool must be approved before execution."""

        if tool_name in self.policy["graylist"]:
            return True
        return bool(
            spec and spec.risk_level == "high" and tool_name not in self.policy["whitelist"]
        )

    def _validate_shell_blacklist(self, payload: dict[str, object]) -> None:
        command = payload.get("command")
        if not isinstance(command, str):
            return
        lowered = command.lower()
        for pattern in self.policy["shell_blacklist"]:
            if pattern.lower() in lowered:
                raise ToolError(f"blacklisted_command: {pattern}", "blocked_by_policy")

    def _validate_parameter_limits(self, tool_name: str, payload: dict[str, object]) -> None:
        limits = dict(self.policy["parameter_limits"].get(tool_name, {}))
        for field, value in payload.items():
            field_limits = dict(limits.get(field, {}))
            max_length = field_limits.get("max_length")
            if max_length is not None and isinstance(value, str) and len(value) > int(max_length):
                raise ToolError(f"parameter_limit_exceeded: {field}", "invalid_arguments")

    @staticmethod
    def _normalize_policy(raw: dict[str, object]) -> dict[str, object]:
        whitelist = list(raw.get("whitelist", raw.get("allow_tools", [])))
        graylist = list(raw.get("graylist", raw.get("review_tools", [])))
        blacklist = list(raw.get("blacklist", []))
        shell_blacklist = list(raw.get("shell_blacklist", raw.get("deny_patterns", [])))
        parameter_limits = dict(raw.get("parameter_limits", {}))
        raw["whitelist"] = whitelist
        raw["graylist"] = graylist
        raw["blacklist"] = blacklist
        raw["shell_blacklist"] = shell_blacklist
        raw["parameter_limits"] = parameter_limits
        return raw
