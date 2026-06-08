"""Sandbox execution defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SandboxPolicy:
    """Describe sandbox requirements for high-risk tools."""

    image: str = "python:3.11-slim"
    timeout_seconds: int = 10
    max_output_chars: int = 8000
    mount_target: str = "/workspace"

    def requires_docker(self, tool_name: str) -> bool:
        """Return whether a tool must execute in Docker."""

        return tool_name in {"python_exec", "shell_exec"}
