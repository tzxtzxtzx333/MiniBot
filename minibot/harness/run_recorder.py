"""Run trace persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class RunRecorder:
    """Persist per-run JSON traces into `.minibot/runs/`."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def start_run(self, message) -> dict[str, object]:
        """Create a new run record and persist its initial state."""

        run_id = str(uuid4())
        task_id = message.metadata.get("task_id") if message.metadata else None
        record = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "channel": message.channel,
            "session_id": message.session_id,
            "user_id": message.user_id,
            "task_id": task_id,
            "user_input": message.content,
            "context_summary": "",
            "context_metrics": {},
            "tool_calls": [],
            "tool_results": [],
            "tool_trace": [],
            "hook_results": [],
            "model_plan": None,
            "subagent_trace": [],
            "final_response": None,
            "final_answer_mode": None,
            "final_answer_model_provider": None,
            "final_answer_model_name": None,
            "final_answer_used_tool_results": False,
            "final_answer_error": None,
            "raw_final_answer_output": None,
            "verifier_reason": None,
            "failure_category": None,
            "retry_count": 0,
            "retry_errors": [],
            "partial_success": False,
            "downgrade_reason": None,
            "cleaned_placeholders": 0,
            "cleaned_placeholder_items": [],
            "compression_events": [],
            "lifecycle_events": [],
            "input": message.content,
            "response": None,
            "max_tool_rounds": 0,
            "actual_tool_rounds": 0,
            "multi_round": False,
            "tool_rounds_detail": [],
            "stop_reason": None,
            "max_tool_calls_total": 0,
            "actual_tool_calls_total": 0,
            "max_runtime_seconds": 0,
            "actual_runtime_seconds": 0.0,
            "max_same_tool_calls": 0,
        }
        self._write(record)
        return record

    def append_event(self, run_id: str, event: str) -> None:
        """Append one lifecycle event to the persisted run trace."""

        record = self._read(run_id)
        record["lifecycle_events"].append(event)
        self._write(record)

    def finish_run(
        self,
        run_id: str,
        response: str,
        model_plan: dict[str, object] | None,
        context_summary: str,
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
        tool_trace: list[dict[str, object]],
        subagent_trace: list[dict[str, object]],
        hook_results: list[dict[str, object]],
        verifier_reason: str | None,
        failure_category: str | None,
        retry_count: int,
        retry_errors: list[str],
        partial_success: bool,
        downgrade_reason: str | None,
        final_answer_mode: str | None = None,
        final_answer_model_provider: str | None = None,
        final_answer_model_name: str | None = None,
        final_answer_used_tool_results: bool = False,
        final_answer_error: str | None = None,
        raw_final_answer_output: str | None = None,
        context_metrics: dict[str, object] | None = None,
        cleaned_placeholders: list[dict[str, object]] | None = None,
        compression_events: list[dict[str, object]] | None = None,
        max_tool_rounds: int = 0,
        actual_tool_rounds: int = 0,
        multi_round: bool = False,
        tool_rounds_detail: list[dict[str, object]] | None = None,
        stop_reason: str | None = None,
        max_tool_calls_total: int = 0,
        actual_tool_calls_total: int = 0,
        max_runtime_seconds: int = 0,
        actual_runtime_seconds: float = 0.0,
        max_same_tool_calls: int = 0,
    ) -> None:
        """Update a run record with the final response."""

        record = self._read(run_id)
        record["ended_at"] = datetime.now(timezone.utc).isoformat()
        record["context_summary"] = context_summary
        record["model_plan"] = model_plan
        record["tool_calls"] = tool_calls
        record["tool_results"] = tool_results
        record["tool_trace"] = tool_trace
        record["subagent_trace"] = subagent_trace
        record["hook_results"] = hook_results
        record["final_response"] = response
        record["final_answer_mode"] = final_answer_mode
        record["final_answer_model_provider"] = final_answer_model_provider
        record["final_answer_model_name"] = final_answer_model_name
        record["final_answer_used_tool_results"] = final_answer_used_tool_results
        record["final_answer_error"] = final_answer_error
        record["raw_final_answer_output"] = raw_final_answer_output
        record["verifier_reason"] = verifier_reason
        record["failure_category"] = failure_category
        record["retry_count"] = retry_count
        record["retry_errors"] = list(retry_errors)
        record["partial_success"] = partial_success
        record["downgrade_reason"] = downgrade_reason
        record["context_metrics"] = dict(context_metrics or {})
        placeholder_items = list(cleaned_placeholders or [])
        record["cleaned_placeholders"] = len(placeholder_items)
        record["cleaned_placeholder_items"] = placeholder_items
        record["compression_events"] = list(compression_events or [])
        record["response"] = response
        record["max_tool_rounds"] = max_tool_rounds
        record["actual_tool_rounds"] = actual_tool_rounds
        record["multi_round"] = multi_round
        record["tool_rounds_detail"] = list(tool_rounds_detail or [])
        record["stop_reason"] = stop_reason
        record["max_tool_calls_total"] = max_tool_calls_total
        record["actual_tool_calls_total"] = actual_tool_calls_total
        record["max_runtime_seconds"] = max_runtime_seconds
        record["actual_runtime_seconds"] = actual_runtime_seconds
        record["max_same_tool_calls"] = max_same_tool_calls
        self._write(record)

    def _read(self, run_id: str) -> dict[str, object]:
        path = self.runs_dir / f"{run_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, record: dict[str, object]) -> None:
        path = self.runs_dir / f"{record['run_id']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
