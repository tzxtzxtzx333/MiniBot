"""Model clients and planning contracts used by AgentLoop."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from minibot.channels.base import ChannelMessage


@dataclass(slots=True)
class ToolCall:
    """Normalized model-emitted tool call."""

    tool_name: str
    arguments: dict[str, object]

    def to_trace(self) -> dict[str, object]:
        return {"tool_name": self.tool_name, "arguments": self.arguments}


@dataclass(slots=True)
class ModelPlan:
    """Planning output from the model layer."""

    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_plan: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ModelFinalAnswer:
    """Final answer synthesis output from the model layer."""

    content: str
    raw_final_output: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_error: str | None = None
    final_answer_mode: str = "fake"
    final_answer_used_tool_results: bool = False


class BaseModelClient:
    """Base contract for model planning and response generation."""

    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:
        raise NotImplementedError

    def plan_next(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
        round_index: int,
    ) -> ModelPlan:
        """Re-plan after observing tool results from a previous round.

        Default: return an empty plan (no more tools needed). Subclasses
        that support multi-round execution should override this method.
        """
        return ModelPlan(
            assistant_message=None,
            tool_calls=[],
            raw_plan={
                "mode": "plan_next",
                "reason": "no_more_tools_needed",
                "tool_calls": [],
            },
        )

    def finalize(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
    ) -> ModelFinalAnswer:
        return ModelFinalAnswer(
            content=self._fallback_final_response(message, tool_results),
            raw_final_output=None,
            model_provider=None,
            model_name=None,
            model_error=None,
            final_answer_mode="fake",
            final_answer_used_tool_results=bool(tool_calls or tool_results),
        )

    def finalize_response(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        plan: ModelPlan,
        tool_results: list[dict[str, object]],
    ) -> str:
        if plan.assistant_message is not None and not tool_results:
            return plan.assistant_message
        return self.finalize(
            message=message,
            context=context,
            tool_calls=[call.to_trace() for call in plan.tool_calls],
            tool_results=tool_results,
        ).content

    def _fallback_final_response(
        self,
        message: ChannelMessage,
        tool_results: list[dict[str, object]],
    ) -> str:
        if tool_results:
            first_approval_required = next(
                (item for item in tool_results if item.get("status") == "approval_required"), None
            )
            if first_approval_required is not None:
                metadata = dict(first_approval_required.get("metadata", {}))
                return (
                    f"MiniBot tool approval required: {first_approval_required.get('tool_name', 'unknown_tool')} "
                    f"approval_id={metadata.get('approval_id', '')}"
                ).strip()
            first_approval_rejected = next(
                (item for item in tool_results if item.get("status") == "approval_rejected"), None
            )
            if first_approval_rejected is not None:
                return f"MiniBot tool blocked: {first_approval_rejected.get('tool_name', 'unknown_tool')} approval_rejected"
            success_items = [item for item in tool_results if item.get("status") == "success"]
            failed_items = [
                item for item in tool_results if item.get("status") in {"failed", "blocked"}
            ]
            if success_items and failed_items:
                success_summaries: list[str] = []
                for item in success_items:
                    success_summaries.append(self._summarize_success_item(item))
                failure_summaries = ", ".join(
                    f"{item.get('tool_name', 'unknown_tool')}={item.get('error') or item.get('failure_category') or item.get('status')}"
                    for item in failed_items
                )
                return (
                    "MiniBot partial success: "
                    f"completed {', '.join(success_summaries)}; "
                    f"failed {failure_summaries}; "
                    "downgrade: review failed tools or use fallback data"
                )
            if len(success_items) > 1 and not failed_items:
                return f"MiniBot tool results: {', '.join(self._summarize_success_item(item) for item in success_items)}"
            first_blocked = next(
                (item for item in tool_results if item.get("status") == "blocked"), None
            )
            if first_blocked is not None:
                return f"MiniBot tool blocked: {first_blocked.get('tool_name', 'unknown_tool')} {first_blocked.get('error') or 'blocked_by_hook'}"
            first_failure = next(
                (item for item in tool_results if item.get("status") == "failed"), None
            )
            if first_failure is not None:
                if first_failure.get("failure_category") in {
                    "sandbox_required",
                    "requires_sandbox_executor",
                }:
                    return f"MiniBot tool blocked: {first_failure.get('tool_name', 'unknown_tool')} {first_failure.get('error') or 'unknown_error'}"
                return f"MiniBot tool failed: {first_failure.get('tool_name', 'unknown_tool')} failed with {first_failure.get('error') or 'unknown_error'}"
            first_success = next(
                (item for item in tool_results if item.get("status") == "success"), None
            )
            if first_success is not None:
                output = first_success.get("output") or {}
                if bool(dict(first_success.get("metadata", {})).get("downgraded")):
                    return f"MiniBot downgraded tool result: {output}"
                if isinstance(output, dict) and "result" in output:
                    return f"MiniBot tool result: {output['result']}"
                return f"MiniBot tool result: {output}"
        return f"MiniBot echo: {message.content}"

    @staticmethod
    def _summarize_success_item(item: dict[str, object]) -> str:
        output = item.get("output")
        if isinstance(output, dict):
            if "result" in output:
                return f"{item.get('tool_name', 'unknown_tool')}={output['result']}"
            stdout = output.get("stdout")
            stderr = output.get("stderr")
            if isinstance(stdout, str) and stdout.strip() and not stderr:
                return f"{item.get('tool_name', 'unknown_tool')}={stdout.strip()}"
        return str(item.get("tool_name", "unknown_tool"))


class FakeModelClient(BaseModelClient):
    """Deterministic model client for local development and tests."""

    _calculator_pattern = re.compile(
        r"(?:计算|calculate|calculator)\s*[:：]?\s*([0-9\(\)\+\-\*/\.\s]+)", re.IGNORECASE
    )
    _file_write_pattern = re.compile(
        r"(?:写入|write)\s+([^\s]+)\s+(?:内容|content)\s*[:：]?\s*(.+)$", re.IGNORECASE
    )
    _file_read_pattern = re.compile(r"(?:读取|read)\s+([^\s]+)$", re.IGNORECASE)
    _memory_write_pattern = re.compile(r"(?:记住|remember)\s*[“\"]?(.+?)[”\"]?$", re.IGNORECASE)
    _memory_search_pattern = re.compile(
        r"(?:搜索记忆|search memory)\s*[“\"]?(.+?)[”\"]?$", re.IGNORECASE
    )
    _doc_summarize_pattern = re.compile(
        r"(?:总结(?:这段文本)?|summarize)\s*[:：]?\s*(.+)$", re.IGNORECASE
    )
    _weather_pattern = re.compile(r"(?:查询天气|天气)\s+(.+)$", re.IGNORECASE)
    _web_search_pattern = re.compile(r"(?:搜索网页|web search)\s+(.+)$", re.IGNORECASE)
    _map_route_pattern = re.compile(
        r"(?:规划路线|map route)\s+(.+?)\s+(?:到|to)\s+(.+)$", re.IGNORECASE
    )
    _web_fetch_pattern = re.compile(r"(?:抓取网页|fetch)\s+(https?://\S+)$", re.IGNORECASE)
    _python_exec_pattern = re.compile(
        r"(?:运行python代码|python_exec|run python code)\s+(.+)$", re.IGNORECASE
    )
    _shell_exec_pattern = re.compile(r"(?:执行shell命令|shell_exec)\s+(.+)$", re.IGNORECASE)
    _multi_tool_pattern = re.compile(
        r"(?:同时)\s*(?:计算)\s*([0-9\(\)\+\-\*/\.\s]+?)\s*(?:并|and)\s*(?:运行python代码|执行python代码)\s+(.+)$",
        re.IGNORECASE,
    )
    _multi_tool_english_pattern = re.compile(
        r"(?:simultaneously\s+)?calculate\s*([0-9\(\)\+\-\*/\.\s]+?)\s*(?:and)\s*(?:run python code)\s+(.+)$",
        re.IGNORECASE,
    )
    _multi_tool_cn_compact_pattern = re.compile(
        r"同时\s*计算\s*([0-9\(\)\+\-\*/\.\s]+?)\s*并\s*(?:运行|执行)\s*python\s*代码\s+(.+)$",
        re.IGNORECASE,
    )
    _multi_tool_weather_failure_pattern = re.compile(
        r"(?:simultaneously\s+)?calculate\s*([0-9\(\)\+\-\*/\.\s]+?)\s*and\s*simulate failed weather\s+(.+)$",
        re.IGNORECASE,
    )
    _multi_tool_shell_pattern = re.compile(
        r"(?:simultaneously\s+)?calculate\s*([0-9\(\)\+\-\*/\.\s]+?)\s*and\s*shell_exec\s+(.+)$",
        re.IGNORECASE,
    )
    _multi_tool_shell_cn_pattern = re.compile(
        r"同时\s*计算\s*([0-9\(\)\+\-\*/\.\s]+?)\s*并\s*(?:执行|运行)\s*shell\s*命令\s+(.+)$",
        re.IGNORECASE,
    )

    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:
        content = message.content.strip()
        multi_tool = self._extract_multi_tool(content)
        if multi_tool is not None:
            return ModelPlan(
                assistant_message=None,
                tool_calls=multi_tool,
                raw_plan={
                    "mode": "tool_call",
                    "reason": "multi_tool_requested",
                    "tool_calls": [call.to_trace() for call in multi_tool],
                },
            )
        weather_failure = self._extract_weather_failure(content)
        if weather_failure is not None:
            return self._tool_plan(
                "weather_retry_downgrade_requested", ToolCall("weather", weather_failure)
            )
        expression = self._extract_expression(content)
        if expression is not None:
            return self._tool_plan(
                "calculation_expression_detected",
                ToolCall("calculator", {"expression": expression}),
            )
        file_write = self._extract_file_write(content)
        if file_write is not None:
            reason = (
                "file_write_requested"
                if {"path", "content"} <= set(file_write.keys())
                else "file_write_invalid_arguments"
            )
            return self._tool_plan(reason, ToolCall("file_write", file_write))
        file_read = self._extract_file_read(content)
        if file_read is not None:
            return self._tool_plan("file_read_requested", ToolCall("file_read", file_read))
        memory_write = self._extract_memory_write(content)
        if memory_write is not None:
            return self._tool_plan(
                "memory_write_requested", ToolCall("memory_write", {"content": memory_write})
            )
        memory_search = self._extract_memory_search(content)
        if memory_search is not None:
            return self._tool_plan(
                "memory_search_requested", ToolCall("memory_search", {"query": memory_search})
            )
        doc_summarize = self._extract_doc_summarize(content)
        if doc_summarize is not None:
            return self._tool_plan(
                "doc_summarize_requested", ToolCall("doc_summarize", {"text": doc_summarize})
            )
        weather_query = self._extract_weather(content)
        if weather_query is not None:
            return self._tool_plan(
                "weather_requested", ToolCall("weather", {"location": weather_query})
            )
        poi_search = self._extract_map_poi_search(content)
        if poi_search is not None:
            return self._tool_plan(
                "map_poi_search_requested", ToolCall("map_poi_search", poi_search)
            )
        web_query = self._extract_web_search(content)
        if web_query is not None:
            return self._tool_plan(
                "web_search_requested", ToolCall("web_search", {"query": web_query})
            )
        route = self._extract_map_route(content)
        if route is not None:
            return self._tool_plan("map_route_requested", ToolCall("map_route", route))
        fetch_url = self._extract_web_fetch(content)
        if fetch_url is not None:
            return self._tool_plan("web_fetch_requested", ToolCall("web_fetch", {"url": fetch_url}))
        python_code = self._extract_python_exec(content)
        if python_code is not None:
            return self._tool_plan(
                "python_exec_requested", ToolCall("python_exec", {"code": python_code})
            )
        shell_command = self._extract_shell_exec(content)
        if shell_command is not None:
            return self._tool_plan(
                "shell_exec_requested", ToolCall("shell_exec", {"command": shell_command})
            )
        return ModelPlan(
            assistant_message=f"MiniBot echo: {message.content}",
            raw_plan={"mode": "chat", "reason": "no_tool_call_detected", "tool_calls": []},
        )

    def _extract_expression(self, content: str) -> str | None:
        match = self._calculator_pattern.search(content)
        if match is None:
            return None
        expression = " ".join(match.group(1).split())
        return expression or None

    def _extract_file_write(self, content: str) -> dict[str, object] | None:
        match = self._file_write_pattern.search(content)
        if match is not None:
            return {"path": match.group(1).strip(), "content": match.group(2).strip()}

        normalized = content.strip()
        lowered = normalized.lower()
        if normalized.startswith("写入") or lowered.startswith("write"):
            remainder = (
                normalized[2:].strip() if normalized.startswith("写入") else normalized[5:].strip()
            )
            if not remainder:
                return {}
            if remainder.startswith("内容"):
                return {"content": remainder[2:].strip()}
            if remainder.lower().startswith("content"):
                return {"content": remainder[7:].strip()}
            parts = remainder.split(None, 1)
            if not parts:
                return {}
            path = parts[0].strip()
            if len(parts) == 1:
                return {"path": path}
            trailing = parts[1].strip()
            if trailing.startswith("内容"):
                content_value = trailing[2:].strip()
                return {"path": path, "content": content_value} if content_value else {"path": path}
            if trailing.lower().startswith("content"):
                content_value = trailing[7:].strip()
                return {"path": path, "content": content_value} if content_value else {"path": path}
            return {"path": path}
        return None

    def _extract_file_read(self, content: str) -> dict[str, object] | None:
        match = self._file_read_pattern.search(content)
        if match is None:
            return None
        return {"path": match.group(1).strip()}

    def _extract_memory_write(self, content: str) -> str | None:
        match = self._memory_write_pattern.search(content)
        if match is None:
            return None
        remembered = match.group(1).strip().strip('“”"')
        return remembered or None

    def _extract_memory_search(self, content: str) -> str | None:
        match = self._memory_search_pattern.search(content)
        if match is None:
            return None
        query = match.group(1).strip().strip('“”"')
        return query or None

    def _extract_doc_summarize(self, content: str) -> str | None:
        match = self._doc_summarize_pattern.search(content)
        if match is None:
            return None
        text = match.group(1).strip()
        return text or None

    def _extract_weather(self, content: str) -> str | None:
        match = self._weather_pattern.search(content)
        if match:
            return match.group(1).strip()
        english = re.search(r"(?:weather)\s+(.+)$", content, re.IGNORECASE)
        return english.group(1).strip() if english else None

    def _extract_web_search(self, content: str) -> str | None:
        match = self._web_search_pattern.search(content)
        return match.group(1).strip() if match else None

    def _extract_map_poi_search(self, content: str) -> dict[str, object] | None:
        normalized = content.strip()
        if "附近" not in normalized:
            return None
        keyword_candidates = [
            "医院",
            "咖啡店",
            "咖啡馆",
            "药店",
            "便利店",
            "餐厅",
            "餐馆",
            "酒店",
        ]
        keyword = next((item for item in keyword_candidates if item in normalized), None)
        if keyword is None:
            return None
        prefix_removed = re.sub(
            r"^(帮我查一下|帮我查|查找|查询|搜索|帮忙查一下)\s*", "", normalized
        )
        location_part, _, trailing = prefix_removed.partition("附近")
        location = location_part.strip(" ，,。.?？")
        keyword_text = re.sub(r"^(有什么|有啥|的)", "", trailing).strip(" ，,。.?？")
        if not keyword_text:
            keyword_text = keyword
        city = ""
        city_match = re.search(r"(.+?)(?:市|区|县)", location)
        if city_match:
            city = city_match.group(1).strip()
        elif "厦门" in normalized:
            city = "厦门"
        if keyword == "咖啡馆":
            keyword = "咖啡店"
        return {
            "query": normalized,
            "location": location,
            "keyword": keyword,
            "city": city,
            "radius": 3000,
        }

    def _extract_map_route(self, content: str) -> dict[str, object] | None:
        match = self._map_route_pattern.search(content)
        if match is None:
            return None
        return {"origin": match.group(1).strip(), "destination": match.group(2).strip()}

    def _extract_web_fetch(self, content: str) -> str | None:
        match = self._web_fetch_pattern.search(content)
        return match.group(1).strip() if match else None

    def _extract_python_exec(self, content: str) -> str | None:
        match = self._python_exec_pattern.search(content)
        return match.group(1).strip() if match else None

    def _extract_shell_exec(self, content: str) -> str | None:
        match = self._shell_exec_pattern.search(content)
        return match.group(1).strip() if match else None

    def _extract_weather_failure(self, content: str) -> dict[str, object] | None:
        english_failure = "simulate failed weather" in content.lower()
        chinese_failure = "天气" in content and "模拟失败" in content
        if not english_failure and not chinese_failure:
            return None
        location = "未知地点"
        explicit = self._extract_weather(content)
        if explicit:
            location = explicit.replace("模拟失败", "").replace("接口", "").strip() or location
        if "厦门" in content:
            location = "厦门"
        if "xiamen" in content.lower():
            location = "Xiamen"
        return {
            "location": location,
            "simulate_failure": "temporary_network_error",
            "needs_advice": True,
        }

    def _extract_multi_tool(self, content: str) -> list[ToolCall] | None:
        match = self._multi_tool_pattern.search(content)
        if match is None:
            match = self._multi_tool_english_pattern.search(content)
        if match is None:
            match = self._multi_tool_cn_compact_pattern.search(content)
        if match is None:
            weather_match = self._multi_tool_weather_failure_pattern.search(content)
            if weather_match is not None:
                expression = " ".join(weather_match.group(1).split())
                location = weather_match.group(2).strip()
                if not expression or not location:
                    return None
                return [
                    ToolCall("calculator", {"expression": expression}),
                    ToolCall(
                        "weather",
                        {
                            "location": location,
                            "simulate_failure": "temporary_network_error",
                            "needs_advice": True,
                        },
                    ),
                ]
            shell_match = self._multi_tool_shell_pattern.search(content)
            if shell_match is None:
                shell_match = self._multi_tool_shell_cn_pattern.search(content)
            if shell_match is None:
                return None
            expression = " ".join(shell_match.group(1).split())
            shell_command = shell_match.group(2).strip()
            if not expression or not shell_command:
                return None
            return [
                ToolCall("calculator", {"expression": expression}),
                ToolCall("shell_exec", {"command": shell_command}),
            ]
        expression = " ".join(match.group(1).split())
        python_code = match.group(2).strip()
        if not expression or not python_code:
            return None
        return [
            ToolCall("calculator", {"expression": expression}),
            ToolCall("python_exec", {"code": python_code}),
        ]

    def _tool_plan(self, reason: str, tool_call: ToolCall) -> ModelPlan:
        return ModelPlan(
            assistant_message=None,
            tool_calls=[tool_call],
            raw_plan={"mode": "tool_call", "reason": reason, "tool_calls": [tool_call.to_trace()]},
        )


class OpenAICompatibleModelClient(BaseModelClient):
    """Thin OpenAI-compatible client using the Python standard library."""

    def __init__(self, base_url: str, api_key: str, model: str, provider: str = "deepseek") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider = provider

    def plan(self, message: ChannelMessage, context: dict[str, object]) -> ModelPlan:
        system_prompt = self._build_system_prompt(
            str(context.get("system_prompt", "")),
            list(context.get("tool_specs", [])),
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ModelPlan(
                assistant_message=f"model_http_error status_code={exc.code}",
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "model_http_error",
                    "tool_calls": [],
                    "model_mode": "real",
                    "model_provider": self.provider,
                    "model_name": self.model,
                    "fake_model": False,
                    "model_error": "model_http_error",
                    "status_code": exc.code,
                    "response_body": body,
                    "request_payload": payload,
                },
            )
        content = response_payload["choices"][0]["message"].get("content", "")
        try:
            maybe_json = json.loads(content)
        except json.JSONDecodeError:
            return ModelPlan(
                assistant_message=content,
                raw_plan={
                    "mode": "openai_compatible",
                    "reason": "tool_parse_error",
                    "tool_calls": [],
                    "model_mode": "real",
                    "model_provider": self.provider,
                    "model_name": self.model,
                    "fake_model": False,
                    "model_error": "tool_parse_error",
                    "raw_model_output": content,
                    "request_payload": payload,
                },
            )

        plan_type = str(maybe_json.get("type", "")).strip().lower()
        tool_calls = [
            ToolCall(tool_name=item["tool_name"], arguments=dict(item.get("arguments", {})))
            for item in maybe_json.get("tool_calls", [])
        ]
        return ModelPlan(
            assistant_message=maybe_json.get("content") or maybe_json.get("assistant_message"),
            tool_calls=tool_calls,
            raw_plan={
                "mode": "openai_compatible",
                "reason": (
                    "delegated_to_model_client"
                    if plan_type != "message"
                    else "message_from_model_client"
                ),
                "tool_calls": [call.to_trace() for call in tool_calls],
                "tool_plan": maybe_json,
                "raw_model_output": content,
                "model_mode": "real",
                "model_provider": self.provider,
                "model_name": self.model,
                "fake_model": False,
                "model_error": None,
                "request_payload": payload,
            },
        )

    def plan_next(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
        round_index: int,
    ) -> ModelPlan:
        """Re-plan after observing tool results from a previous round.

        Sends the original request, already-executed tool calls and their
        results to the model so it can decide whether more tools are needed.
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are continuing a tool-using agent run.\n"
                        "Review the original user request and prior tool results below.\n"
                        'If the task is complete, return {"type":"message","content":"..."}.\n'
                        'If one more tool call is necessary, return {"type":"tool_plan","tool_calls":[...]}.\n'
                        "Do not repeat identical tool calls.\n"
                        "Do not bypass approval_required, approval_rejected, or blocked_by_policy.\n"
                        "If a tool failed, either choose a safe alternative tool or explain the failure.\n"
                        "Keep tool calls minimal.\n"
                        "You must output valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_message": message.content,
                            "round": round_index,
                            "previous_tool_calls": tool_calls,
                            "previous_tool_results": tool_results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ModelPlan(
                assistant_message=f"model_http_error status_code={exc.code}",
                raw_plan={
                    "mode": "plan_next",
                    "reason": "model_http_error",
                    "tool_calls": [],
                    "model_mode": "real",
                    "model_provider": self.provider,
                    "model_name": self.model,
                    "fake_model": False,
                    "model_error": "model_http_error",
                    "status_code": exc.code,
                    "response_body": body,
                },
            )
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return ModelPlan(
                assistant_message=None,
                tool_calls=[],
                raw_plan={
                    "mode": "plan_next",
                    "reason": "replan_error_no_more_tools",
                    "tool_calls": [],
                },
            )

        content = str(response_payload["choices"][0]["message"].get("content", ""))
        try:
            maybe_json = json.loads(content)
        except json.JSONDecodeError:
            return ModelPlan(
                assistant_message=content,
                raw_plan={
                    "mode": "plan_next",
                    "reason": "tool_parse_error",
                    "tool_calls": [],
                    "model_mode": "real",
                    "model_provider": self.provider,
                    "model_name": self.model,
                    "fake_model": False,
                    "model_error": "tool_parse_error",
                    "raw_model_output": content,
                },
            )

        plan_type = str(maybe_json.get("type", "")).strip().lower()
        tool_calls_out = [
            ToolCall(tool_name=item["tool_name"], arguments=dict(item.get("arguments", {})))
            for item in maybe_json.get("tool_calls", [])
        ]
        return ModelPlan(
            assistant_message=maybe_json.get("content") or maybe_json.get("assistant_message"),
            tool_calls=tool_calls_out,
            raw_plan={
                "mode": "openai_compatible",
                "reason": (
                    "replan_delegated_to_model_client"
                    if plan_type != "message"
                    else "replan_message_from_model_client"
                ),
                "tool_calls": [call.to_trace() for call in tool_calls_out],
                "tool_plan": maybe_json,
                "raw_model_output": content,
                "model_mode": "real",
                "model_provider": self.provider,
                "model_name": self.model,
                "fake_model": False,
                "model_error": None,
                "round_index": round_index,
            },
        )

    def finalize(
        self,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
    ) -> ModelFinalAnswer:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are generating the final answer after tools have already run.\n"
                        "Do not call tools again.\n"
                        "Use tool results as evidence.\n"
                        "If a tool was blocked or approval is required, explain safely and concisely.\n"
                        "If partial success occurred, explain what succeeded and what failed.\n"
                        "Respond in natural language for the end user."
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_finalize_user_prompt(
                        message=message,
                        context=context,
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                    ),
                },
            ],
            "temperature": 0,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ModelFinalAnswer(
                content=self._fallback_final_response(message, tool_results),
                raw_final_output=body,
                model_provider=self.provider,
                model_name=self.model,
                model_error="model_http_error",
                final_answer_mode="real",
                final_answer_used_tool_results=bool(tool_calls or tool_results),
            )
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return ModelFinalAnswer(
                content=self._fallback_final_response(message, tool_results),
                raw_final_output=None,
                model_provider=self.provider,
                model_name=self.model,
                model_error="final_answer_synthesis_error",
                final_answer_mode="real",
                final_answer_used_tool_results=bool(tool_calls or tool_results),
            )

        content = str(response_payload["choices"][0]["message"].get("content", "")).strip()
        if not content:
            return ModelFinalAnswer(
                content=self._fallback_final_response(message, tool_results),
                raw_final_output=content,
                model_provider=self.provider,
                model_name=self.model,
                model_error="empty_final_answer",
                final_answer_mode="real",
                final_answer_used_tool_results=bool(tool_calls or tool_results),
            )
        return ModelFinalAnswer(
            content=content,
            raw_final_output=content,
            model_provider=self.provider,
            model_name=self.model,
            model_error=None,
            final_answer_mode="real",
            final_answer_used_tool_results=bool(tool_calls or tool_results),
        )

    @staticmethod
    def _build_system_prompt(base_prompt: str, tool_specs: list[object]) -> str:
        tool_lines: list[str] = []
        for item in tool_specs:
            spec = dict(item) if isinstance(item, dict) else {}
            name = str(spec.get("name", "")).strip()
            if not name:
                continue
            description = str(spec.get("description", "")).strip()
            parameters = json.dumps(
                spec.get("input_schema", {}), ensure_ascii=False, sort_keys=True
            )
            tool_lines.append(f"- {name}: {description} parameters={parameters}")
        sections = [base_prompt.strip()] if base_prompt.strip() else []
        sections.extend(
            [
                "You must output valid JSON.",
                'If a tool is needed, output JSON like: {"type":"tool_plan","tool_calls":[{"tool_name":"calculator","arguments":{"expression":"128 * 64"}}]}',
                'If no tool is needed, output JSON like: {"type":"message","content":"..."}',
                "Available tools:",
                *tool_lines,
            ]
        )
        return "\n".join(sections)

    @staticmethod
    def _build_finalize_user_prompt(
        *,
        message: ChannelMessage,
        context: dict[str, object],
        tool_calls: list[dict[str, object]],
        tool_results: list[dict[str, object]],
    ) -> str:
        history = str(context.get("history", "")).strip()
        memory = str(context.get("memory", "")).strip()
        archives = list(context.get("archives", []))
        compact_context = {
            "history": history[-1500:] if history else "",
            "memory": memory[-1200:] if memory else "",
            "archive_count": len(archives),
        }
        return json.dumps(
            {
                "user_message": message.content,
                "context": compact_context,
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            },
            ensure_ascii=False,
            indent=2,
        )


def load_model_client(project_root: Path, mode: str):
    normalized = mode.strip().lower()
    if normalized == "fake":
        return FakeModelClient()
    if normalized in {"real", "openai-compatible"}:
        settings = _load_env_settings(project_root)
        return OpenAICompatibleModelClient(
            base_url=settings["MINIBOT_MODEL_BASE_URL"],
            api_key=settings["MINIBOT_MODEL_API_KEY"],
            model=settings["MINIBOT_MODEL_NAME"],
            provider=settings["MINIBOT_MODEL_PROVIDER"],
        )
    raise ValueError(f"unsupported model mode: {mode}")


def _load_env_settings(project_root: Path) -> dict[str, str]:
    settings = _parse_dotenv(project_root / ".env")
    merged = {
        **settings,
        **{key: value for key, value in os.environ.items() if key.startswith("MINIBOT_")},
    }
    if not merged.get("MINIBOT_MODEL_PROVIDER"):
        merged["MINIBOT_MODEL_PROVIDER"] = "deepseek"
    if not merged.get("MINIBOT_MODEL_BASE_URL") and merged.get("MINIBOT_BASE_URL"):
        merged["MINIBOT_MODEL_BASE_URL"] = merged["MINIBOT_BASE_URL"]
    if not merged.get("MINIBOT_MODEL_API_KEY") and merged.get("MINIBOT_API_KEY"):
        merged["MINIBOT_MODEL_API_KEY"] = merged["MINIBOT_API_KEY"]
    required = [
        "MINIBOT_MODEL_PROVIDER",
        "MINIBOT_MODEL_BASE_URL",
        "MINIBOT_MODEL_API_KEY",
        "MINIBOT_MODEL_NAME",
    ]
    missing = [name for name in required if not merged.get(name)]
    if missing:
        raise RuntimeError(f"deepseek_config_missing: {', '.join(missing)}")
    return merged


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result
