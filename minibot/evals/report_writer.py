"""Benchmark report writer for JSON and Markdown output."""

from __future__ import annotations

import json
from pathlib import Path


class ReportWriter:
    """Persist benchmark reports in both machine and human readable forms."""

    def write(self, path: Path, report: dict[str, object]) -> dict[str, Path]:
        """Write JSON and Markdown report files."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path = path.with_suffix(".md")
        markdown_path.write_text(self._to_markdown(report), encoding="utf-8")
        return {"json": path, "markdown": markdown_path}

    def _to_markdown(self, report: dict[str, object]) -> str:
        lines = [
            "# MiniBot Benchmark Report",
            "",
            f"- phase: {report.get('phase')}",
            f"- run_mode: {report.get('run_mode')}",
            f"- benchmark_scope: {report.get('benchmark_scope')}",
            f"- benchmark_profile: {report.get('benchmark_profile')}",
            f"- benchmark_case_count: {report.get('benchmark_case_count')}",
            f"- real_agent_case_count: {report.get('real_agent_case_count')}",
            f"- real_agent_passed_count: {report.get('real_agent_passed_count')}",
            f"- real_agent_pass_rate: {report.get('real_agent_pass_rate')}",
            f"- context_case_count: {report.get('context_case_count')}",
            f"- avg_prompt_tokens: {report.get('avg_prompt_tokens')}",
            f"- avg_context_chars: {report.get('avg_context_chars')}",
            f"- avg_dynamic_context_chars: {report.get('avg_dynamic_context_chars')}",
            f"- avg_dynamic_context_tokens: {report.get('avg_dynamic_context_tokens')}",
            f"- avg_history_chars: {report.get('avg_history_chars')}",
            f"- avg_memory_chars: {report.get('avg_memory_chars')}",
            f"- avg_archive_chars: {report.get('avg_archive_chars')}",
            f"- avg_tool_specs_chars: {report.get('avg_tool_specs_chars')}",
            f"- token_estimator: {report.get('token_estimator')}",
            f"- model_provider: {report.get('model_provider')}",
            f"- model_name: {report.get('model_name')}",
            f"- fake_model: {report.get('fake_model')}",
            f"- verifier_mode: {report.get('verifier_mode')}",
            f"- fake_verifier: {report.get('fake_verifier')}",
            f"- verifier_provider: {report.get('verifier_provider')}",
            f"- verifier_model_name: {report.get('verifier_model_name')}",
            f"- verifier_config_source: {report.get('verifier_config_source')}",
            f"- docker_available: {report.get('docker_available')}",
            f"- total_cases: {report.get('total_cases')}",
            f"- counted_cases: {report.get('counted_cases')}",
            f"- passed_cases: {report.get('passed_cases')}",
            f"- pass_rate: {report.get('pass_rate')}",
            f"- avg_latency: {report.get('avg_latency')}",
            f"- avg_tool_rounds: {report.get('avg_tool_rounds')}",
            f"- tool_rounds: {report.get('tool_rounds')}",
            f"- retry_count: {report.get('retry_count')}",
            f"- partial_success: {report.get('partial_success')}",
            f"- missing_capabilities: {', '.join(report.get('missing_capabilities', [])) or 'none'}",
            f"- mock_tools_used: {', '.join(report.get('mock_tools_used', [])) or 'none'}",
            f"- real_tools_used: {', '.join(report.get('real_tools_used', [])) or 'none'}",
            f"- mcp_tools_used: {', '.join(report.get('mcp_tools_used', [])) or 'none'}",
            "",
            "## Benchmark Catalog",
            "",
        ]
        for name, count in dict(report.get("benchmark_case_count_by_profile", {})).items():
            lines.append(f"- profile.{name}: {count}")
        for name, count in dict(report.get("benchmark_case_count_by_category", {})).items():
            lines.append(f"- category.{name}: {count}")
        lines.extend([
            "",
            "## Capability Status",
            "",
        ])
        for capability, status in dict(report.get("capability_status", {})).items():
            lines.append(f"- {capability}: {status}")
        human_review = dict(report.get("human_review", {}))
        lines.extend(["", "## Human Review", ""])
        lines.append(f"- pending_count: {human_review.get('pending_count', 0)}")
        lines.append(f"- approved_count: {human_review.get('approved_count', 0)}")
        lines.append(f"- rejected_count: {human_review.get('rejected_count', 0)}")
        lines.append(f"- safety_case_count: {report.get('safety_case_count', 0)}")
        lines.append(f"- safety_passed_count: {report.get('safety_passed_count', 0)}")
        lines.append(f"- safety_pass_rate: {report.get('safety_pass_rate', 0.0)}")
        lines.extend(["", "## External Integrations", ""])
        for name, status in dict(report.get("external_integrations", {})).items():
            lines.append(f"- {name}: {status}")
        lines.extend(["", "## Results", "", "| id | category | status | verifier_reason |", "| --- | --- | --- | --- |"])
        for item in report.get("results", []):
            lines.append(
                f"| {item.get('id')} | {item.get('category')} | {item.get('status')} | {str(item.get('verifier_reason', '')).replace('|', '/')} |"
            )
        return "\n".join(lines) + "\n"
