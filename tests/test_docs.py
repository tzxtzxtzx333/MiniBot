"""Documentation, scripts, and evidence validation tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestEnvExample:
    def test_env_example_exists(self) -> None:
        path = ROOT / ".env.example"
        assert path.exists()

    def test_env_example_contains_key_variables(self) -> None:
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        # Model
        assert "MINIBOT_MODEL_MODE=" in content
        assert "MINIBOT_MODEL_PROVIDER=" in content
        assert "MINIBOT_MODEL_BASE_URL=" in content
        assert "MINIBOT_MODEL_API_KEY=" in content
        assert "MINIBOT_MODEL_NAME=" in content
        # Verifier
        assert "MINIBOT_VERIFIER_MODE=" in content
        # Provider
        assert "MINIBOT_WEB_SEARCH_PROVIDER=" in content
        assert "TAVILY_API_KEY=" in content
        assert "MINIBOT_WEATHER_PROVIDER=" in content
        assert "MINIBOT_MAP_PROVIDER=" in content
        # Feishu
        assert "FEISHU_APP_ID=" in content
        assert "FEISHU_APP_SECRET=" in content
        # Budget
        assert "MINIBOT_AGENT_PROFILE=" in content
        assert "MINIBOT_MAX_TOOL_ROUNDS=" in content
        assert "MINIBOT_MAX_TOOL_CALLS_TOTAL=" in content
        assert "MINIBOT_MAX_RUNTIME_SECONDS=" in content
        assert "MINIBOT_MAX_SAME_TOOL_CALLS=" in content
        # HTTP Auth
        assert "MINIBOT_HTTP_AUTH_TOKEN=" in content

    def test_env_example_does_not_contain_real_keys(self) -> None:
        """No real API keys should be present in the committed .env.example."""
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        # These should be empty or commented out
        lines_with_real_keys = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for key_var in (
                "MINIBOT_MODEL_API_KEY",
                "MINIBOT_VERIFIER_API_KEY",
                "TAVILY_API_KEY",
                "MINIBOT_WEATHER_API_KEY",
                "MINIBOT_AMAP_MCP_API_KEY",
                "FEISHU_APP_ID",
                "FEISHU_APP_SECRET",
            ):
                if stripped.startswith(f"{key_var}="):
                    value = stripped.split("=", 1)[1] if "=" in stripped else ""
                    if value and not value.startswith("your-") and len(value) > 5:
                        lines_with_real_keys.append(stripped)
        assert lines_with_real_keys == [], f"possible real keys found: {lines_with_real_keys}"


class TestScripts:
    SCRIPTS = [
        "run_http.ps1",
        "run_feishu.ps1",
        "run_real_agent_benchmark.ps1",
        "run_safety_benchmark.ps1",
        "run_multiround_benchmark.ps1",
        "run_status.ps1",
    ]

    def test_all_scripts_exist(self) -> None:
        scripts_dir = ROOT / "scripts"
        assert scripts_dir.is_dir()
        for name in self.SCRIPTS:
            assert (scripts_dir / name).exists(), f"missing script: {name}"

    def test_scripts_do_not_contain_real_keys(self) -> None:
        scripts_dir = ROOT / "scripts"
        for name in self.SCRIPTS:
            content = (scripts_dir / name).read_text(encoding="utf-8")
            assert "sk-" not in content.lower(), f"{name} may contain an API key"
            assert "tvly-" not in content.lower(), f"{name} may contain a Tavily key"


class TestReadmeKeywords:
    def test_readme_contains_taskstore_section(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "TaskStore" in content
        assert "tasks create" in content
        assert "tasks resume" in content

    def test_readme_contains_approval_api_section(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "Approval API" in content
        assert "GET /approvals" in content or "GET  /approvals" in content
        assert "/approve" in content
        assert "/reject" in content

    def test_readme_contains_status_health_check_section(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "Status Health Check" in content
        assert "task_count" in content
        assert "approval_pending_count" in content
        assert "budget" in content

    def test_readme_contains_deployment_boundary_section(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "Deployment Boundary" in content
        assert "run_http.ps1" in content
        assert ".env.example" in content

    def test_readme_contains_final_positioning(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "任务状态" in content
        assert "渠道内审批" in content
        assert "部署运行边界" in content
        assert "Agent Harness 应用雏形" in content


class TestFinalMetrics:
    def test_final_metrics_contains_all_profiles(self) -> None:
        content = (ROOT / "docs" / "evidence" / "final_metrics.md").read_text(encoding="utf-8")
        assert "real-agent profile" in content
        assert "safety" in content
        assert "multiround" in content
        assert "TaskStore" in content
        assert "HTTP Approval API" in content
        assert "Status health check" in content

    def test_final_metrics_has_evidence_reports(self) -> None:
        content = (ROOT / "docs" / "evidence" / "final_metrics.md").read_text(encoding="utf-8")
        assert "run_real_agent.json" in content
        assert "run_fake_safety_check.json" in content
        assert "run_fake_multiround.json" in content


class TestEvidenceReports:
    """Check that evidence reports exist or are explicitly noted as missing."""

    EVIDENCE_FILES = [
        "run_real_agent.json",
        "run_fake_safety_check.json",
        "run_fake_multiround.json",
    ]

    def test_evidence_directory_exists(self) -> None:
        assert (ROOT / "docs" / "evidence").is_dir()

    def test_evidence_files_exist(self) -> None:
        evidence_dir = ROOT / "docs" / "evidence"
        for name in self.EVIDENCE_FILES:
            path = evidence_dir / name
            assert path.exists(), f"missing evidence file: {name} — run benchmark to generate"

    def test_evidence_files_are_valid_json(self) -> None:
        evidence_dir = ROOT / "docs" / "evidence"
        for name in self.EVIDENCE_FILES:
            path = evidence_dir / name
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    assert isinstance(payload, dict), f"{name} is not a JSON object"
                except json.JSONDecodeError:
                    assert False, f"{name} is not valid JSON"
