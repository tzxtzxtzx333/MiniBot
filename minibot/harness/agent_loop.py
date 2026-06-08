"""Core request loop for MiniBot."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from minibot.channels.base import ChannelMessage
from minibot.harness.model_client import BaseModelClient, ModelFinalAnswer, ModelPlan


@dataclass(slots=True)
class AgentLoopResult:
    """Result returned from a single harness run."""

    run_id: str
    response: str
    tool_trace: list[dict[str, object]] = field(default_factory=list)
    verifier_reason: str | None = None


class AgentLoop:
    """Harness loop that drives context, planning, tool usage, and trace persistence."""

    def __init__(
        self,
        model_client,
        context_builder,
        tool_dispatcher,
        memory_store,
        recorder,
        hook_manager,
        memory_agent,
        tool_agent,
        verifier_agent,
        chat_turn_limit: int = 20,
        budget=None,
        archive_token_budget: int = 900,
    ) -> None:
        from minibot.config import AgentBudgetProfile

        self.model_client = model_client
        self.context_builder = context_builder
        self.tool_dispatcher = tool_dispatcher
        self.memory_store = memory_store
        self.recorder = recorder
        self.hook_manager = hook_manager
        self.memory_agent = memory_agent
        self.tool_agent = tool_agent
        self.verifier_agent = verifier_agent
        self.chat_turn_limit = chat_turn_limit
        self.budget = budget if isinstance(budget, AgentBudgetProfile) else AgentBudgetProfile()
        self.archive_token_budget = archive_token_budget

    def handle_message(self, message: ChannelMessage) -> AgentLoopResult:
        """Process one normalized channel message through the harness lifecycle."""

        run = self.recorder.start_run(message)
        run_id = str(run["run_id"])
        tool_calls: list[dict[str, object]] = []
        tool_results: list[dict[str, object]] = []
        tool_trace: list[dict[str, object]] = []
        hook_results: list[dict[str, object]] = []
        failure_category: str | None = None
        final_response = ""
        compression_events: list[dict[str, object]] = []
        cleaned_placeholders: list[dict[str, object]] = []
        retry_count = 0
        retry_errors: list[str] = []
        partial_success = False
        downgrade_reason: str | None = None
        subagent_trace: list[dict[str, object]] = []
        verifier_reason: str | None = None
        final_answer = ModelFinalAnswer(content="", final_answer_mode="fake", final_answer_used_tool_results=False)
        actual_tool_rounds = 0
        multi_round = False
        tool_rounds_detail: list[dict[str, object]] = []
        stop_reason: str | None = None
        actual_tool_calls_total = 0
        actual_runtime_seconds = 0.0

        self._event(run_id, "SessionStart")
        hook_results.extend(self._trigger_hooks("SessionStart", "SessionStart", {"run_id": run_id}))
        self._event(run_id, "UserMessageReceived")
        hook_results.extend(
            self._trigger_hooks("UserMessageReceived", message.content, {"run_id": run_id, "value": message.content})
        )
        self._event(run_id, "MemoryRecall")
        context = self.context_builder.build(message)
        recalled_text = "\n".join(str(item) for item in context.get("recalled_memories", []))
        hook_results.extend(self._trigger_hooks("MemoryRecall", recalled_text, {"run_id": run_id, "value": recalled_text}))
        self._event(run_id, "ContextBuild")
        cleaned_context = self.context_builder.clean(context)
        cleaned_context["tool_specs"] = self._tool_specs()
        context_metrics = self.context_builder.measure(cleaned_context)
        context_blob = cleaned_context.get("history", "")
        hook_results.extend(self._trigger_hooks("ContextBuild", str(context_blob), {"run_id": run_id, "value": str(context_blob)}))
        self._event(run_id, "PlaceholderClean")
        cleaned_placeholders = list(cleaned_context.get("_clean_meta", {}).get("cleaned_placeholders", []))
        context_summary = self.context_builder.summarize(cleaned_context)
        memory_decision = self.memory_agent.assess(message.content)
        subagent_trace.append(
            {
                "agent": "MemoryAgent",
                "action": "assess",
                "status": "completed",
                "metadata": memory_decision,
            }
        )

        if message.content.strip() == "/new":
            self._event(run_id, "ModelPlanning")
            plan = self._command_plan("new_session_requested")
            self._event(run_id, "VerifierCheck")
            compacted = self.memory_store.compact_history(
                source_session_id=message.session_id,
                compression_trigger="user_new_command",
            )
            if compacted is not None:
                compression_events.append(compacted)
                subagent_trace.append(
                    {
                        "agent": "SummarizerAgent",
                        "action": "compact_history",
                        "status": "completed",
                        "metadata": compacted,
                    }
                )
            final_response = "MiniBot archived the recent session and started a new history window."
        else:
            self._event(run_id, "ModelPlanning")
            plan = self.model_client.plan(message=message, context=cleaned_context)
            round_tool_calls = self.tool_agent.extract_tool_calls(plan)
            subagent_trace.append(
                {
                    "agent": "ToolAgent",
                    "action": "extract_tool_calls",
                    "status": "completed",
                    "metadata": {"tool_call_count": len(round_tool_calls)},
                }
            )

            # --- Budget-aware multi-round observe → re-plan loop ---
            all_tool_calls: list[dict[str, object]] = []
            all_tool_results: list[dict[str, object]] = []
            all_tool_trace: list[dict[str, object]] = []
            tool_rounds_detail: list[dict[str, object]] = []
            actual_tool_rounds = 0
            multi_round = False
            round_index = 1
            round_plan = plan
            run_started = time.perf_counter()
            same_tool_counter: dict[str, int] = {}
            stop_reason: str | None = None
            actual_tool_calls_total = 0
            exec_round_tool_calls = round_tool_calls  # mutable per-round

            while exec_round_tool_calls and round_index <= self.budget.max_tool_rounds:
                actual_tool_rounds = round_index

                # --- runtime budget check ---
                elapsed = time.perf_counter() - run_started
                if elapsed >= self.budget.max_runtime_seconds:
                    stop_reason = "max_runtime_reached"
                    break

                # --- total tool calls budget check ---
                if actual_tool_calls_total + len(exec_round_tool_calls) > self.budget.max_tool_calls_total:
                    stop_reason = "max_tool_calls_reached"
                    break

                self._event(run_id, "ToolCallDetected")
                self._event(run_id, "PreToolUse")
                pre_tool_hook_results = self._trigger_hooks(
                    "PreToolUse",
                    exec_round_tool_calls[0]["tool_name"],
                    {"run_id": run_id, "tool_calls": exec_round_tool_calls, "value": exec_round_tool_calls[0]["tool_name"]},
                )
                hook_results.extend(pre_tool_hook_results)
                blocked_by_hook = any(item.get("blocked") for item in pre_tool_hook_results)
                if blocked_by_hook:
                    blocking_hook = next((item for item in pre_tool_hook_results if item.get("blocked")), {})
                    block_reason = str(blocking_hook.get("message") or "blocked_by_hook")
                    if failure_category is None:
                        failure_category = "approval_denied" if block_reason == "approval_denied" else "blocked_by_hook"
                    round_tool_results = [
                        {
                            "tool_name": exec_round_tool_calls[0]["tool_name"],
                            "status": "blocked",
                            "error": block_reason,
                        }
                    ]
                    round_tool_trace = [
                        {
                            "tool_name": exec_round_tool_calls[0]["tool_name"],
                            "arguments": exec_round_tool_calls[0]["arguments"],
                            "status": "blocked",
                            "error": block_reason,
                        }
                    ]
                else:
                    self._event(run_id, "ToolGovernanceCheck")
                    self._event(run_id, "ToolExecution")
                    execution = self.tool_agent.execute_plan(
                        round_plan,
                        dispatch_context={
                            "channel": message.channel,
                            "user_id": message.user_id,
                            "session_id": message.session_id,
                        },
                    )
                    exec_round_tool_calls = execution["tool_calls"]
                    round_tool_results = execution["tool_results"]
                    round_tool_trace = execution["tool_trace"]
                    dispatch_meta = dict(execution["dispatcher_metadata"])
                    subagent_trace.append(
                        {
                            "agent": "ToolAgent",
                            "action": "dispatch_tool_calls",
                            "status": "completed",
                            "metadata": {
                                "tool_call_count": len(exec_round_tool_calls),
                                "tool_result_count": len(round_tool_results),
                            },
                        }
                    )
                    retry_count = int(dispatch_meta.get("retry_count", 0))
                    retry_errors = list(dispatch_meta.get("retry_errors", []))
                    partial_success = bool(dispatch_meta.get("partial_success", False))
                    downgrade_reason = dispatch_meta.get("downgrade_reason")
                    if dispatch_meta.get("failure_category") is not None:
                        failure_category = str(dispatch_meta["failure_category"])

                actual_tool_calls_total += len(exec_round_tool_calls)

                self._event(run_id, "PostToolUse")
                post_tool_payload = json_dumps(round_tool_results)
                post_tool_results = self._trigger_hooks(
                    "PostToolUse",
                    post_tool_payload,
                    {"run_id": run_id, "value": post_tool_payload},
                )
                hook_results.extend(post_tool_results)
                round_tool_results = self._apply_redactions_to_tool_results(round_tool_results, post_tool_results)
                self._event(run_id, "ToolResultAppend")

                blocked_tool = next((item for item in round_tool_results if item.get("status") == "blocked"), None)
                if blocked_tool is not None:
                    failure_category = str(blocked_tool.get("failure_category") or blocked_tool.get("error") or "blocked")
                failed_tool = next((item for item in round_tool_results if item.get("status") == "failed"), None)
                if failed_tool is not None:
                    if failure_category is None:
                        failure_category = str(failed_tool.get("failure_category") or "tool_execution_failed")
                    hook_results.extend(
                        self._trigger_hooks(
                            "ToolError",
                            str(failed_tool.get("error", "")),
                            {"run_id": run_id, "value": str(failed_tool.get("error", ""))},
                        )
                    )

                # --- check same-tool-call loop ---
                for call in exec_round_tool_calls:
                    key = self._canonical_call_key(call.get("tool_name", ""), dict(call.get("arguments", {})))
                    same_tool_counter[key] = same_tool_counter.get(key, 0) + 1
                    if same_tool_counter[key] > self.budget.max_same_tool_calls:
                        stop_reason = "duplicate_loop_detected"
                        break

                # --- stop checks ---
                any_blocked = any(r.get("status") == "blocked" for r in round_tool_results)
                any_approval_required = any(r.get("status") == "approval_required" for r in round_tool_results)
                any_approval_rejected = any(r.get("status") == "approval_rejected" for r in round_tool_results)

                # Determine round_stop_reason
                round_stop: str | None = None
                if stop_reason:
                    round_stop = stop_reason
                elif any_blocked:
                    round_stop = "blocked_by_policy"
                elif any_approval_required:
                    round_stop = "approval_required"
                elif any_approval_rejected:
                    round_stop = "approval_rejected"

                # Accumulate results from this round
                round_detail = {
                    "round_index": round_index,
                    "plan_mode": str(dict(round_plan.raw_plan).get("mode", "")),
                    "plan_reason": str(dict(round_plan.raw_plan).get("reason", "")),
                    "tool_calls": list(exec_round_tool_calls),
                    "tool_results": list(round_tool_results),
                    "observation_summary": self._observation_summary(round_tool_results),
                    "round_stop_reason": round_stop,
                }
                tool_rounds_detail.append(round_detail)
                all_tool_calls.extend(exec_round_tool_calls)
                all_tool_results.extend(round_tool_results)
                all_tool_trace.extend(round_tool_trace)

                # --- decide whether to re-plan ---
                if stop_reason or any_blocked or any_approval_required or any_approval_rejected:
                    if stop_reason is None:
                        stop_reason = round_stop
                    exec_round_tool_calls = []
                elif round_index < self.budget.max_tool_rounds:
                    plan_next = self.model_client.plan_next(
                        message=message,
                        context=cleaned_context,
                        tool_calls=all_tool_calls,
                        tool_results=all_tool_results,
                        round_index=round_index + 1,
                    )
                    exec_round_tool_calls = self.tool_agent.extract_tool_calls(plan_next)
                    if exec_round_tool_calls:
                        self._event(run_id, "ToolObservation")
                        self._event(run_id, "ModelRePlanning")
                    else:
                        stop_reason = "no_more_tools"
                    round_plan = plan_next
                else:
                    stop_reason = "max_tool_rounds_reached"
                    exec_round_tool_calls = []

                round_index += 1

            # Post-loop: resolve stop_reason
            if stop_reason is None:
                stop_reason = "no_more_tools"

            if actual_tool_rounds > 1:
                multi_round = True

            actual_runtime_seconds = round(time.perf_counter() - run_started, 4)

            # Use accumulated results for finalize
            tool_calls = all_tool_calls
            tool_results = all_tool_results
            tool_trace = all_tool_trace

            self._event(run_id, "VerifierCheck")
            if tool_calls or tool_results:
                self._event(run_id, "FinalAnswerSynthesis")
                try:
                    final_answer = self.model_client.finalize(
                        message=message,
                        context=cleaned_context,
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                    )
                except Exception:  # noqa: BLE001
                    final_answer = ModelFinalAnswer(
                        content=BaseModelClient()._fallback_final_response(message, tool_results),
                        final_answer_mode="fake",
                        final_answer_used_tool_results=bool(tool_calls or tool_results),
                        model_error="final_answer_synthesis_error",
                    )
                final_response = final_answer.content
            else:
                final_response = self.model_client.finalize_response(
                    message=message,
                    context=cleaned_context,
                    plan=plan,
                    tool_results=tool_results,
                )
                final_answer = ModelFinalAnswer(
                    content=final_response,
                    final_answer_mode="fake" if dict(plan.raw_plan).get("model_mode") != "real" else "real",
                    final_answer_used_tool_results=False,
                )
            verification = self.verifier_agent.verify(
                final_response=final_response,
                user_goal=message.content,
                expected_behavior=None,
                tool_results=tool_results,
            )
            verifier_reason = str(verification["verifier_reason"])
            subagent_trace.append(
                {
                    "agent": "VerifierAgent",
                    "action": "verify_response",
                    "status": "completed",
                    "metadata": verification,
                }
            )

        hook_results.extend(
            self._trigger_hooks("BeforeResponse", final_response, {"run_id": run_id, "value": final_response})
        )
        final_response = self._apply_redactions_to_value(final_response, hook_results)
        self._event(run_id, "FinalResponseGenerate")

        if bool(memory_decision.get("store_memory")) and memory_decision.get("memory_fact"):
            stored = self.memory_store.write_memory_fact(str(memory_decision["memory_fact"]), include_timestamp=False)
            subagent_trace.append(
                {
                    "agent": "MemoryAgent",
                    "action": "persist_memory_fact",
                    "status": "completed" if stored else "skipped",
                    "metadata": {"memory_fact": memory_decision["memory_fact"]},
                }
            )
        self.memory_store.append_history(message, final_response)
        if message.content.strip() != "/new":
            if self.memory_store.turn_count() > self.chat_turn_limit:
                compacted = self.memory_store.compact_history(
                    source_session_id=message.session_id,
                    compression_trigger="turn_limit_exceeded",
                )
                if compacted is not None:
                    compression_events.append(compacted)
                    subagent_trace.append(
                        {
                            "agent": "SummarizerAgent",
                            "action": "compact_history",
                            "status": "completed",
                            "metadata": compacted,
                        }
                    )
            elif self.memory_store.history_token_count() > self.archive_token_budget:
                compacted = self.memory_store.compact_history(
                    source_session_id=message.session_id,
                    compression_trigger="token_budget_exceeded",
                )
                if compacted is not None:
                    compression_events.append(compacted)
                    subagent_trace.append(
                        {
                            "agent": "SummarizerAgent",
                            "action": "compact_history",
                            "status": "completed",
                            "metadata": compacted,
                        }
                    )
        self._event(run_id, "HistoryPersist")
        hook_results.extend(
            self._trigger_hooks("AfterResponse", final_response, {"run_id": run_id, "value": final_response})
        )
        final_response = self._apply_redactions_to_value(final_response, hook_results)

        self.recorder.finish_run(
            run_id,
            response=final_response,
            model_plan=plan.raw_plan,
            context_summary=context_summary,
            tool_calls=tool_calls,
            tool_results=tool_results,
            tool_trace=tool_trace,
            subagent_trace=subagent_trace,
            hook_results=hook_results,
            verifier_reason=verifier_reason,
            failure_category=failure_category,
            retry_count=retry_count,
            retry_errors=retry_errors,
            partial_success=partial_success,
            downgrade_reason=downgrade_reason,
            final_answer_mode=final_answer.final_answer_mode,
            final_answer_model_provider=final_answer.model_provider,
            final_answer_model_name=final_answer.model_name,
            final_answer_used_tool_results=final_answer.final_answer_used_tool_results,
            final_answer_error=final_answer.model_error,
            raw_final_answer_output=final_answer.raw_final_output,
            context_metrics=context_metrics,
            cleaned_placeholders=cleaned_placeholders,
            compression_events=compression_events,
            max_tool_rounds=self.budget.max_tool_rounds,
            actual_tool_rounds=actual_tool_rounds,
            multi_round=multi_round,
            tool_rounds_detail=tool_rounds_detail,
            stop_reason=stop_reason,
            max_tool_calls_total=self.budget.max_tool_calls_total,
            actual_tool_calls_total=actual_tool_calls_total,
            max_runtime_seconds=self.budget.max_runtime_seconds,
            actual_runtime_seconds=actual_runtime_seconds,
            max_same_tool_calls=self.budget.max_same_tool_calls,
        )
        self._event(run_id, "RunReportPersist")
        self._event(run_id, "SessionEnd")
        hook_results.extend(self._trigger_hooks("SessionEnd", "SessionEnd", {"run_id": run_id}))
        self.recorder.finish_run(
            run_id,
            response=final_response,
            model_plan=plan.raw_plan,
            context_summary=context_summary,
            tool_calls=tool_calls,
            tool_results=tool_results,
            tool_trace=tool_trace,
            subagent_trace=subagent_trace,
            hook_results=hook_results,
            verifier_reason=verifier_reason,
            failure_category=failure_category,
            retry_count=retry_count,
            retry_errors=retry_errors,
            partial_success=partial_success,
            downgrade_reason=downgrade_reason,
            final_answer_mode=final_answer.final_answer_mode,
            final_answer_model_provider=final_answer.model_provider,
            final_answer_model_name=final_answer.model_name,
            final_answer_used_tool_results=final_answer.final_answer_used_tool_results,
            final_answer_error=final_answer.model_error,
            raw_final_answer_output=final_answer.raw_final_output,
            context_metrics=context_metrics,
            cleaned_placeholders=cleaned_placeholders,
            compression_events=compression_events,
            max_tool_rounds=self.budget.max_tool_rounds,
            actual_tool_rounds=actual_tool_rounds,
            multi_round=multi_round,
            tool_rounds_detail=tool_rounds_detail,
            stop_reason=stop_reason,
            max_tool_calls_total=self.budget.max_tool_calls_total,
            actual_tool_calls_total=actual_tool_calls_total,
            max_runtime_seconds=self.budget.max_runtime_seconds,
            actual_runtime_seconds=actual_runtime_seconds,
            max_same_tool_calls=self.budget.max_same_tool_calls,
        )
        return AgentLoopResult(run_id=run_id, response=final_response, tool_trace=tool_trace, verifier_reason=verifier_reason)

    def _event(self, run_id: str, event: str) -> None:
        self.recorder.append_event(run_id, event)

    def _trigger_hooks(self, event: str, match_value: str, context: dict[str, object]) -> list[dict[str, object]]:
        return self.hook_manager.trigger(event=event, match_value=match_value, context=context)

    def _apply_redactions_to_value(self, value: str, hook_results: list[dict[str, object]]) -> str:
        updated = value
        for result in hook_results:
            if result.get("redacted_fields") == ["value"] and result.get("updated_value") is not None:
                updated = str(result["updated_value"])
        return updated

    def _apply_redactions_to_tool_results(
        self,
        tool_results: list[dict[str, object]],
        hook_results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        updated_results = tool_results
        for result in hook_results:
            if result.get("redacted_fields") == ["value"] and result.get("updated_value") is not None:
                try:
                    import json

                    updated_results = json.loads(str(result["updated_value"]))
                except Exception:  # noqa: BLE001
                    return updated_results
        return updated_results

    def _command_plan(self, reason: str) -> ModelPlan:
        return ModelPlan(
            assistant_message=None,
            tool_calls=[],
            raw_plan={
                "mode": "command",
                "reason": reason,
                "tool_calls": [],
            },
        )

    @staticmethod
    def _canonical_call_key(tool_name: str, arguments: dict[str, object]) -> str:
        import json

        return json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _observation_summary(tool_results: list[dict[str, object]]) -> str:
        if not tool_results:
            return "0 results"
        statuses: dict[str, int] = {}
        for item in tool_results:
            status = str(item.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
        parts = [f"{count} {status}" for status, count in sorted(statuses.items())]
        return ", ".join(parts)

    def _tool_specs(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": dict(spec.input_schema),
                "risk_level": spec.risk_level,
                "sandbox_required": spec.sandbox_required,
                "timeout": spec.timeout,
            }
            for spec in self.tool_dispatcher.registry.list_tools()
        ]


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
