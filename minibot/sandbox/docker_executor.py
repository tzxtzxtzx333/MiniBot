"""Docker-backed sandbox execution for high-risk tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

from minibot.tools.base import ToolResult

from .policies import SandboxPolicy


class DockerSandboxExecutor:
    """Execute Python and shell tools inside an isolated Docker container."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def available(self) -> bool:
        """Return whether Docker is available on the host."""

        try:
            result = subprocess.run(
                ["docker", "info"],
                check=False,
                capture_output=True,
                timeout=3,
                text=True,
                encoding="utf-8",
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def execute(
        self,
        tool_name: str,
        payload: dict[str, object],
        workspace_root: Path,
        timeout: int | None = None,
    ) -> ToolResult:
        """Run a supported high-risk tool in Docker or fail safely."""

        effective_timeout = timeout or self.policy.timeout_seconds
        workspace_root.mkdir(parents=True, exist_ok=True)

        if not self.available():
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error="docker_unavailable",
                failure_category="docker_unavailable",
                metadata={
                    "sandbox_required": True,
                    "sandbox": "docker",
                    "docker_available": False,
                    "container_image": self.policy.image,
                    "timeout": effective_timeout,
                    "output_truncated": False,
                    "blocked_by_policy": False,
                },
            )

        if tool_name == "python_exec":
            command = ["python", "-c", str(payload["code"])]
        elif tool_name == "shell_exec":
            command = ["sh", "-lc", str(payload["command"])]
        else:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error="unsupported_sandbox_tool",
                failure_category="sandbox_execution_failed",
                metadata={
                    "sandbox_required": True,
                    "sandbox": "docker",
                    "docker_available": True,
                    "container_image": self.policy.image,
                    "timeout": effective_timeout,
                    "output_truncated": False,
                    "blocked_by_policy": False,
                },
            )

        docker_command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{workspace_root.resolve()}:{self.policy.mount_target}",
            "-w",
            self.policy.mount_target,
            self.policy.image,
            *command,
        ]
        try:
            result = subprocess.run(
                docker_command,
                check=False,
                capture_output=True,
                timeout=effective_timeout,
                text=True,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error="tool_timeout",
                failure_category="tool_timeout",
                metadata={
                    "sandbox_required": True,
                    "sandbox": "docker",
                    "docker_available": True,
                    "container_image": self.policy.image,
                    "timeout": effective_timeout,
                    "output_truncated": False,
                    "blocked_by_policy": False,
                },
            )
        except subprocess.SubprocessError as exc:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=str(exc),
                failure_category="sandbox_execution_failed",
                metadata={
                    "sandbox_required": True,
                    "sandbox": "docker",
                    "docker_available": True,
                    "container_image": self.policy.image,
                    "timeout": effective_timeout,
                    "output_truncated": False,
                    "blocked_by_policy": False,
                },
            )

        stdout = self._limit_output(result.stdout)
        stderr = self._limit_output(result.stderr)
        metadata = {
            "sandbox_required": True,
            "sandbox": "docker",
            "docker_available": True,
            "container_image": self.policy.image,
            "timeout": effective_timeout,
            "exit_code": result.returncode,
            "output_truncated": len(result.stdout) > self.policy.max_output_chars
            or len(result.stderr) > self.policy.max_output_chars,
            "blocked_by_policy": False,
        }
        if result.returncode != 0:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output={"stdout": stdout, "stderr": stderr, "returncode": result.returncode},
                error=stderr or stdout or "sandbox_execution_failed",
                failure_category="sandbox_execution_failed",
                metadata=metadata,
            )
        return ToolResult(
            tool_name=tool_name,
            success=True,
            output={"stdout": stdout, "stderr": stderr, "returncode": result.returncode},
            error=None,
            failure_category=None,
            metadata=metadata,
        )

    def _limit_output(self, value: str) -> str:
        if len(value) <= self.policy.max_output_chars:
            return value
        return value[: self.policy.max_output_chars]
