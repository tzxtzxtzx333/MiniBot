"""StepVerifier — determine whether a plan step completed successfully."""

from __future__ import annotations

from .plan_schema import Step


class StepVerifier:
    """Lightweight step outcome verification.

    Fake mode uses rule-based checks (tool success, non-empty response,
    file-write detection).  Real mode can optionally delegate to an
    external verifier agent.
    """

    def __init__(self, *, mode: str = "fake", external_verifier=None) -> None:
        self.mode = mode
        self._external_verifier = external_verifier

    def verify(
        self,
        step: Step,
        run_record: dict[str, object],
    ) -> dict[str, object]:
        """Return a verdict dict with ``status``, ``reason``, and ``failure_category``.

        Expected keys in the return value:
        * ``status`` — ``"completed"``, ``"failed"``, ``"waiting_approval"``
        * ``reason`` — human-readable explanation
        * ``failure_category`` — optional category from tool trace
        """
        failure_category = str(run_record.get("failure_category") or "")
        tool_results = list(run_record.get("tool_results", []))
        final_response = str(run_record.get("final_response") or "")
        stop_reason = str(run_record.get("stop_reason") or "")

        # --- approval_required ---
        for tr in tool_results:
            if tr.get("status") == "approval_required":
                return {
                    "status": "waiting_approval",
                    "reason": f"step {step.step_id} requires approval for {tr.get('tool_name')}",
                    "failure_category": "approval_required",
                }
            if tr.get("failure_category") == "approval_rejected":
                return {
                    "status": "failed",
                    "reason": f"step {step.step_id} approval was rejected",
                    "failure_category": "approval_rejected",
                }

        # --- blocked_by_policy ---
        if failure_category == "blocked_by_policy":
            return {
                "status": "failed",
                "reason": f"step {step.step_id} blocked by policy",
                "failure_category": "blocked_by_policy",
            }

        # --- explicit tool failures ---
        for tr in tool_results:
            if tr.get("status") in {"failed", "blocked"}:
                return {
                    "status": "failed",
                    "reason": f"step {step.step_id} tool {tr.get('tool_name')} failed: {tr.get('error', 'unknown')}",
                    "failure_category": str(tr.get("failure_category", "tool_execution_failed")),
                }

        # --- budget stop reasons ---
        if stop_reason in {
            "max_tool_rounds_reached",
            "max_tool_calls_reached",
            "max_runtime_reached",
            "duplicate_loop_detected",
        }:
            return {
                "status": "failed",
                "reason": f"step {step.step_id} stopped: {stop_reason}",
                "failure_category": stop_reason,
            }

        # --- file_write verification ---
        if (
            step.expected_output
            and "文件" in step.expected_output
            and "file_write" in str(step.tool_hints)
        ):
            file_write_success = any(
                tr.get("tool_name") == "file_write" and tr.get("status") == "success"
                for tr in tool_results
            )
            if not file_write_success:
                return {
                    "status": "failed",
                    "reason": f"step {step.step_id} expected file_write to succeed but it did not",
                    "failure_category": "file_write_not_found",
                }

        # --- success: tool succeeded and response is non-empty ---
        if final_response.strip():
            return {
                "status": "completed",
                "reason": f"step {step.step_id} completed",
                "failure_category": None,
            }

        # --- fallback: if tool succeeded but response empty, still count as completed ---
        any_success = any(tr.get("status") == "success" for tr in tool_results)
        if any_success:
            return {
                "status": "completed",
                "reason": f"step {step.step_id} completed (tool success, no text response)",
                "failure_category": None,
            }

        return {
            "status": "failed",
            "reason": f"step {step.step_id} produced no successful output",
            "failure_category": failure_category or "no_output",
        }
