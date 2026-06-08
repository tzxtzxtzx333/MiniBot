"""Tool dispatch, governance, sandbox routing, and retry handling."""

from __future__ import annotations

import json
from pathlib import Path

from minibot.governance.approval import ApprovalManager
from minibot.governance.approval_store import ApprovalStore
from minibot.governance.duplicate_detector import DuplicateCallDetector
from minibot.governance.partial_success import PartialSuccessHandler
from minibot.governance.redactor import SensitiveInfoRedactor
from minibot.governance.retry_manager import RetryManager
from minibot.sandbox.docker_executor import DockerSandboxExecutor
from minibot.sandbox.policies import SandboxPolicy
from minibot.tools.base import BaseTool, ToolError, ToolResult, blocked_tool_result
from minibot.tools.calculator import CalculatorTool
from minibot.tools.doc_summarize import DocSummarizeTool
from minibot.tools.file_ops import FileReadTool, FileWriteTool
from minibot.tools.map_poi_search import MapPoiSearchTool
from minibot.tools.map_route import MapRouteTool
from minibot.tools.memory_tools import MemorySearchTool, MemoryWriteTool
from minibot.tools.python_exec import PythonExecTool
from minibot.tools.registry import ToolRegistry
from minibot.tools.shell_exec import ShellExecTool
from minibot.tools.weather import WeatherTool
from minibot.tools.web_fetch import WebFetchTool
from minibot.tools.web_search import WebSearchTool


class ToolDispatcher:
    """Dispatch tool calls through validation, governance, retry, and execution."""

    def __init__(
        self,
        policy_manager,
        project_root: Path,
        workspace,
        memory_store,
        memory_recall,
        registry: ToolRegistry | None = None,
        approval_manager: ApprovalManager | None = None,
        redactor: SensitiveInfoRedactor | None = None,
        duplicate_detector: DuplicateCallDetector | None = None,
        retry_manager: RetryManager | None = None,
        partial_success_handler: PartialSuccessHandler | None = None,
        docker_executor: DockerSandboxExecutor | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        self.policy_manager = policy_manager
        self.project_root = project_root
        self.workspace = workspace
        self.memory_store = memory_store
        self.memory_recall = memory_recall
        self.registry = registry or self._build_default_registry()
        self.approval_manager = approval_manager or ApprovalManager(self.policy_manager.policy)
        self.redactor = redactor or SensitiveInfoRedactor(list(self.policy_manager.policy.get("sensitive_patterns", [])))
        dedupe_enabled = bool(dict(self.policy_manager.policy.get("dedupe", {})).get("enabled", True))
        self.duplicate_detector = duplicate_detector or DuplicateCallDetector(enabled=dedupe_enabled)
        self.retry_manager = retry_manager or RetryManager(self.policy_manager.policy)
        self.partial_success_handler = partial_success_handler or PartialSuccessHandler()
        self.sandbox_policy = sandbox_policy or SandboxPolicy()
        self.docker_executor = docker_executor or DockerSandboxExecutor(self.sandbox_policy)
        self.approval_store = approval_store or ApprovalStore(self.workspace.approvals_dir)
        self.last_execution_metadata = {
            "retry_count": 0,
            "retry_errors": [],
            "downgrade_reason": None,
            "partial_success": False,
            "failure_category": None,
        }

    def dispatch(
        self,
        tool_calls: list[dict[str, object]],
        dispatch_context: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """Execute a batch of normalized tool calls."""

        dispatch_context = dict(dispatch_context or {})
        self.duplicate_detector.reset()
        self.last_execution_metadata = {
            "retry_count": 0,
            "retry_errors": [],
            "downgrade_reason": None,
            "partial_success": False,
            "failure_category": None,
        }
        results: list[dict[str, object]] = []
        trace: list[dict[str, object]] = []

        for call in tool_calls:
            tool_name = str(call["tool_name"])
            arguments = dict(call.get("arguments", {}))
            signature = self.duplicate_detector.signature(tool_name, arguments)
            duplicate = self.duplicate_detector.lookup(signature)
            if duplicate is not None:
                deduped = self._deduplicated_result(duplicate)
                result_record = self._redact_result_record(deduped.to_result_record())
                trace_record = self._redact_trace_record(deduped.to_trace_record(arguments))
                results.append(result_record)
                trace.append(trace_record)
                continue

            try:
                tool = self.registry.get(tool_name)
                spec = tool.spec
                self.registry.validate_input(tool_name, arguments)
                self.policy_manager.validate(tool_name, arguments, spec)
                self._preflight_validate(tool, arguments)
                requires_approval = self.policy_manager.requires_approval(tool_name, spec)
                approval_metadata: dict[str, object] = {}
                resolved_approval = None
                if requires_approval:
                    resolved_approval = self.approval_store.find_resolution(
                        user_id=str(dispatch_context.get("user_id", "")),
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                    if resolved_approval is not None:
                        approval_metadata = {
                            "approval_required": False,
                            "approval_status": str(resolved_approval.get("status", "")),
                            "approval_id": str(resolved_approval.get("approval_id", "")),
                            "risk_level": "gray",
                        }
                        if str(resolved_approval.get("status")) == "rejected":
                            tool_result = ToolResult(
                                tool_name=tool_name,
                                success=False,
                                output=None,
                                error="approval_rejected",
                                failure_category="approval_rejected",
                                metadata=approval_metadata,
                            )
                            result_record = self._redact_result_record(tool_result.to_result_record())
                            trace_record = self._redact_trace_record(tool_result.to_trace_record(arguments))
                            self.duplicate_detector.remember(signature, result_record)
                            results.append(result_record)
                            trace.append(trace_record)
                            continue
                approval = self.approval_manager.decide(tool_name, requires_approval=requires_approval)
                if requires_approval and resolved_approval is None and not approval.approved:
                    redacted_arguments, redacted_fields = self.redactor.redact_value(arguments)
                    pending = self.approval_store.create_pending(
                        session_id=str(dispatch_context.get("session_id", "")),
                        user_id=str(dispatch_context.get("user_id", "")),
                        tool_name=tool_name,
                        arguments=dict(redacted_arguments if isinstance(redacted_arguments, dict) else {}),
                        risk_level="gray",
                        reason=approval.reason,
                    )
                    tool_result = ToolResult(
                        tool_name,
                        success=False,
                        output=None,
                        error="approval_required",
                        failure_category="approval_required",
                        metadata={
                            "approval_required": True,
                            "approval_id": str(pending["approval_id"]),
                            "approval_status": "pending",
                            "risk_level": "gray",
                            "redacted_fields": redacted_fields,
                        },
                        status_override="approval_required",
                    )
                else:
                    outcome = self.retry_manager.run(
                        tool=tool,
                        payload=arguments,
                        execute=lambda: self._execute_tool(tool, arguments),
                    )
                    tool_result = outcome.result
                    self.last_execution_metadata["retry_count"] = int(self.last_execution_metadata["retry_count"]) + outcome.retry_count
                    self.last_execution_metadata["retry_errors"].extend(outcome.retry_errors)
                    if outcome.downgrade_reason is not None:
                        self.last_execution_metadata["downgrade_reason"] = outcome.downgrade_reason
                    tool_result.metadata.setdefault("retry_count", outcome.retry_count)
                    tool_result.metadata.setdefault("retry_errors", list(outcome.retry_errors))
                    if outcome.downgrade_reason is not None:
                        tool_result.metadata.setdefault("downgrade_reason", outcome.downgrade_reason)
                    if approval_metadata:
                        tool_result.metadata.update(approval_metadata)
            except ToolError as exc:
                blocked = exc.failure_category == "blocked_by_policy"
                tool_result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=str(exc),
                    failure_category=exc.failure_category,
                    metadata={},
                    status_override="blocked" if blocked else None,
                )
            except Exception as exc:  # noqa: BLE001
                tool_result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=str(exc),
                    failure_category="tool_dispatch_failed",
                    metadata={},
                )

            result_record = self._redact_result_record(tool_result.to_result_record())
            trace_record = self._redact_trace_record(tool_result.to_trace_record(arguments))
            self.duplicate_detector.remember(signature, result_record)
            results.append(result_record)
            trace.append(trace_record)

        partial_summary = self.partial_success_handler.evaluate(results)
        self.last_execution_metadata["partial_success"] = partial_summary.partial_success
        if self.last_execution_metadata["failure_category"] is None:
            approval_required = next((item for item in results if item.get("status") == "approval_required"), None)
            if approval_required is not None:
                self.last_execution_metadata["failure_category"] = approval_required.get("failure_category")
            failed = next((item for item in results if item.get("status") in {"failed", "blocked"}), None)
            if failed is not None:
                self.last_execution_metadata["failure_category"] = failed.get("failure_category")
        return results, trace

    def _execute_tool(self, tool: BaseTool, arguments: dict[str, object]) -> ToolResult:
        if self.sandbox_policy.requires_docker(tool.spec.name):
            return self.docker_executor.execute(
                tool.spec.name,
                arguments,
                self.workspace.sandbox_dir,
                timeout=tool.spec.timeout,
            )
        return tool.execute(arguments)

    @staticmethod
    def _preflight_validate(tool: BaseTool, arguments: dict[str, object]) -> None:
        resolver = getattr(tool, "_resolve_path", None)
        raw_path = arguments.get("path")
        if callable(resolver) and isinstance(raw_path, str):
            resolver(raw_path)

    def _redact_result_record(self, record: dict[str, object]) -> dict[str, object]:
        redacted_output, fields = self.redactor.redact_value(record.get("output"))
        if fields:
            record["output"] = redacted_output
            metadata = dict(record.get("metadata", {}))
            metadata["redacted_fields"] = fields
            record["metadata"] = metadata
        if isinstance(record.get("error"), str):
            redacted_error, error_fields = self.redactor.redact_value(record["error"])
            if error_fields:
                record["error"] = redacted_error
        return record

    def _redact_trace_record(self, record: dict[str, object]) -> dict[str, object]:
        redacted_arguments, argument_fields = self.redactor.redact_value(record.get("arguments"))
        redacted_output, output_fields = self.redactor.redact_value(record.get("output"))
        metadata = dict(record.get("metadata", {}))
        if argument_fields:
            record["arguments"] = redacted_arguments
        if output_fields:
            record["output"] = redacted_output
        if argument_fields or output_fields:
            metadata["redacted_fields"] = argument_fields + output_fields
        record["metadata"] = metadata
        if isinstance(record.get("error"), str):
            redacted_error, error_fields = self.redactor.redact_value(record["error"])
            if error_fields:
                record["error"] = redacted_error
        return record

    @staticmethod
    def _deduplicated_result(previous: dict[str, object]) -> ToolResult:
        metadata = dict(previous.get("metadata", {}))
        metadata["deduplicated"] = True
        return ToolResult(
            tool_name=str(previous["tool_name"]),
            success=bool(previous.get("success")),
            output=previous.get("output"),
            error=previous.get("error"),
            failure_category=previous.get("failure_category"),
            metadata=metadata,
            status_override=str(previous.get("status")) if previous.get("status") not in {None, "success", "failed"} else None,
        )

    def _build_default_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        file_root = self.workspace.sandbox_dir
        registry.register(CalculatorTool())
        registry.register(FileReadTool(file_root))
        registry.register(FileWriteTool(file_root))
        registry.register(WebFetchTool())
        registry.register(WebSearchTool())
        registry.register(WeatherTool())
        registry.register(MapRouteTool())
        registry.register(MapPoiSearchTool())
        registry.register(PythonExecTool())
        registry.register(ShellExecTool())
        registry.register(MemorySearchTool(self.workspace, self.memory_recall))
        registry.register(MemoryWriteTool(self.memory_store))
        registry.register(DocSummarizeTool(file_root))
        return registry
