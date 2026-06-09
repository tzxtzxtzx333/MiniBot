from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from minibot.sandbox.docker_executor import DockerSandboxExecutor
from minibot.sandbox.policies import SandboxPolicy

ROOT = Path(__file__).resolve().parents[1]


def _temp_workspace() -> Path:
    path = ROOT / ".tmp_test_roots" / f"docker-exec-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_python_exec_runs_through_docker_and_returns_metadata(monkeypatch) -> None:
    tmp_path = _temp_workspace()
    calls: list[list[str]] = []
    try:

        def fake_run(command, **kwargs):  # noqa: ANN001
            calls.append(command)
            if command[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="2\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        executor = DockerSandboxExecutor(SandboxPolicy())

        result = executor.execute("python_exec", {"code": "print(1+1)"}, tmp_path, timeout=10)

        assert result.success is True
        assert result.output["stdout"] == "2\n"
        assert result.metadata["sandbox"] == "docker"
        assert result.metadata["docker_available"] is True
        assert result.metadata["container_image"] == "python:3.11-slim"
        assert result.metadata["timeout"] == 10
        assert result.metadata["output_truncated"] is False
        assert result.metadata["blocked_by_policy"] is False
        assert any(cmd[:3] == ["docker", "run", "--rm"] for cmd in calls)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_shell_exec_runs_through_docker_and_returns_metadata(monkeypatch) -> None:
    tmp_path = _temp_workspace()
    try:

        def fake_run(command, **kwargs):  # noqa: ANN001, ARG001
            if command[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="hello\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        executor = DockerSandboxExecutor(SandboxPolicy())

        result = executor.execute("shell_exec", {"command": "echo hello"}, tmp_path, timeout=10)

        assert result.success is True
        assert result.output["stdout"] == "hello\n"
        assert result.metadata["sandbox"] == "docker"
        assert result.metadata["docker_available"] is True
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_docker_unavailable_returns_structured_failure_metadata(monkeypatch) -> None:
    tmp_path = _temp_workspace()
    try:

        def fake_run(command, **kwargs):  # noqa: ANN001, ARG001
            raise FileNotFoundError("docker missing")

        monkeypatch.setattr(subprocess, "run", fake_run)
        executor = DockerSandboxExecutor(SandboxPolicy())

        result = executor.execute("python_exec", {"code": "print(1+1)"}, tmp_path, timeout=10)

        assert result.success is False
        assert result.failure_category == "docker_unavailable"
        assert result.metadata["sandbox"] == "docker"
        assert result.metadata["docker_available"] is False
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_output_is_truncated_and_marked(monkeypatch) -> None:
    tmp_path = _temp_workspace()
    long_output = "x" * 50
    try:

        def fake_run(command, **kwargs):  # noqa: ANN001, ARG001
            if command[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=long_output, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        executor = DockerSandboxExecutor(SandboxPolicy(max_output_chars=10))

        result = executor.execute("python_exec", {"code": "print('x')"}, tmp_path, timeout=10)

        assert result.success is True
        assert result.output["stdout"] == "x" * 10
        assert result.metadata["output_truncated"] is True
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_timeout_returns_structured_failure(monkeypatch) -> None:
    tmp_path = _temp_workspace()
    try:

        def fake_run(command, **kwargs):  # noqa: ANN001, ARG001
            if command[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
            raise subprocess.TimeoutExpired(command, timeout=10)

        monkeypatch.setattr(subprocess, "run", fake_run)
        executor = DockerSandboxExecutor(SandboxPolicy())

        result = executor.execute("shell_exec", {"command": "sleep 20"}, tmp_path, timeout=10)

        assert result.success is False
        assert result.failure_category == "tool_timeout"
        assert result.metadata["sandbox"] == "docker"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
