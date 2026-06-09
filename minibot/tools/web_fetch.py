"""External web tools with provider status metadata."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from .base import BaseTool, ToolResult, ToolSpec, provider_metadata

MAX_RESPONSE_BYTES = 16000
MAX_SNIPPET_CHARS = 4000


class WebFetchTool(BaseTool):
    """Fetch a URL and return a bounded text snippet."""

    spec = ToolSpec(
        name="web_fetch",
        description="Fetch a URL and return page content.",
        input_schema={
            "type": "object",
            "required": ["url"],
            "additionalProperties": False,
            "properties": {"url": {"type": "string"}},
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=10,
        max_retries=0,
    )

    def execute(self, payload: dict[str, object]) -> ToolResult:
        url = str(payload["url"]).strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error="unsupported_url_scheme",
                failure_category="unsupported_url_scheme",
                metadata=provider_metadata(
                    provider="web_fetch",
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=True,
                ),
            )

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "MiniBot/0.1 (+https://example.com)"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.spec.timeout
            ) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
                encoding = "utf-8"
                if "charset=" in content_type:
                    encoding = (
                        content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
                    )
                text = raw[:MAX_RESPONSE_BYTES].decode(encoding, errors="replace")
                truncated = len(raw) > MAX_RESPONSE_BYTES or len(text) > MAX_SNIPPET_CHARS
                snippet = text[:MAX_SNIPPET_CHARS]
                return ToolResult(
                    tool_name=self.spec.name,
                    success=True,
                    output={
                        "url": url,
                        "status_code": int(getattr(response, "status", 200)),
                        "content_type": content_type or "application/octet-stream",
                        "text_snippet": snippet,
                    },
                    metadata=provider_metadata(
                        provider="web_fetch",
                        provider_status="real",
                        mock_provider=False,
                        real_provider=True,
                        output_truncated=truncated,
                    ),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"web_fetch_http_error: {exc.code}",
                failure_category="web_fetch_http_error",
                metadata=provider_metadata(
                    provider="web_fetch",
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=True,
                    status_code=int(exc.code),
                    response_body=body[:MAX_SNIPPET_CHARS],
                ),
            )
        except urllib.error.URLError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"web_fetch_network_error: {exc.reason}",
                failure_category="web_fetch_network_error",
                metadata=provider_metadata(
                    provider="web_fetch",
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=True,
                ),
            )
