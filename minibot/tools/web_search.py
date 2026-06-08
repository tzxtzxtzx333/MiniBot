"""Web search tool with mock and Tavily provider modes."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import BaseTool, ToolResult, ToolSpec

TAVILY_ENDPOINT = "https://api.tavily.com/search"
MAX_ERROR_SNIPPET = 500


def _provider_metadata(
    *,
    provider: str,
    provider_status: str,
    mock_provider: bool,
    real_provider: bool,
    mcp_provider: bool = False,
    **extra: object,
) -> dict[str, object]:
    metadata = {
        "provider": provider,
        "provider_status": provider_status,
        "mock_provider": mock_provider,
        "real_provider": real_provider,
        "mcp_provider": mcp_provider,
    }
    metadata.update(extra)
    return metadata


class WebSearchTool(BaseTool):
    """Search the web using a mock provider or Tavily."""

    spec = ToolSpec(
        name="web_search",
        description="Search the web and return ranked results.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=10,
        max_retries=0,
    )

    def execute(self, payload: dict[str, object]) -> ToolResult:
        query = str(payload["query"]).strip()
        provider = os.getenv("MINIBOT_WEB_SEARCH_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "tavily":
            return self._execute_tavily(query)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={
                "query": query,
                "results": [
                    {
                        "title": f"Mock result for {query}",
                        "url": f"https://example.com/search?q={query}",
                        "snippet": f"Mock search snippet for {query}",
                    }
                ],
            },
            metadata=_provider_metadata(
                provider="web_search",
                provider_status="mock",
                mock_provider=True,
                real_provider=False,
                provider_name="mock",
            ),
        )

    def _execute_tavily(self, query: str) -> ToolResult:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        project = os.getenv("TAVILY_PROJECT", "").strip()
        search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"
        max_results = self._normalize_max_results(os.getenv("TAVILY_MAX_RESULTS", "5"))
        if not api_key:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error="tavily_config_missing",
                failure_category="tavily_config_missing",
                metadata=_provider_metadata(
                    provider="web_search",
                    provider_status="missing",
                    mock_provider=False,
                    real_provider=True,
                    provider_name="tavily",
                    search_depth=search_depth,
                    max_results=max_results,
                ),
            )
        payload = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if project:
            headers["X-Project-ID"] = project
        request = urllib.request.Request(
            TAVILY_ENDPOINT,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        metadata = _provider_metadata(
            provider="web_search",
            provider_status="real",
            mock_provider=False,
            real_provider=True,
            provider_name="tavily",
            search_depth=search_depth,
            max_results=max_results,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.spec.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8", errors="replace")
                body = json.loads(raw)
        except urllib.error.HTTPError as exc:
            snippet = self._read_error_body(exc, api_key)
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error="tavily_http_error",
                failure_category="tavily_http_error",
                metadata={
                    **metadata,
                    "provider_status": "failed",
                    "status_code": int(exc.code),
                    "response_body_snippet": snippet,
                },
            )
        except urllib.error.URLError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"tavily_network_error: {exc.reason}",
                failure_category="tavily_network_error",
                metadata={**metadata, "provider_status": "failed"},
            )
        except json.JSONDecodeError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"tavily_http_error: invalid_json_response: {exc}",
                failure_category="tavily_http_error",
                metadata={**metadata, "provider_status": "failed"},
            )

        results = []
        for item in body.get("results", []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": str(item.get("content") or item.get("snippet") or "").strip(),
                    "score": self._to_float(item.get("score")),
                }
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={"query": query, "results": results},
            metadata=metadata,
        )

    @staticmethod
    def _normalize_max_results(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            value = 5
        return max(1, min(value, 10))

    @staticmethod
    def _read_error_body(exc: urllib.error.HTTPError, api_key: str) -> str:
        body = exc.read().decode("utf-8", errors="replace")
        return body.replace(api_key, "***")[:MAX_ERROR_SNIPPET] if api_key else body[:MAX_ERROR_SNIPPET]

    @staticmethod
    def _to_float(value: object) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
