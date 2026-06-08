"""Weather tool with provider boundary and explicit status metadata."""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zlib
from json import JSONDecodeError

from .base import BaseTool, ToolResult, ToolSpec


def _weather_metadata(
    *,
    provider_status: str,
    mock_provider: bool,
    real_provider: bool,
    **extra: object,
) -> dict[str, object]:
    metadata = {
        "provider": "weather",
        "provider_status": provider_status,
        "mock_provider": mock_provider,
        "real_provider": real_provider,
        "mcp_provider": False,
    }
    metadata.update(extra)
    return metadata


class WeatherTool(BaseTool):
    """Return a mock weather forecast or structured provider-boundary errors."""

    spec = ToolSpec(
        name="weather",
        description="Get weather information for a location.",
        input_schema={
            "type": "object",
            "required": ["location"],
            "additionalProperties": False,
            "properties": {
                "location": {"type": "string"},
                "simulate_failure": {"type": "string"},
                "needs_advice": {"type": "boolean"},
            },
        },
        risk_level="low",
        sandbox_required=False,
        timeout=10,
        max_retries=2,
    )

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}

    def execute(self, payload: dict[str, object]) -> ToolResult:
        provider = os.getenv("MINIBOT_WEATHER_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "real":
            api_key = os.getenv("MINIBOT_WEATHER_API_KEY", "").strip()
            if not api_key:
                return ToolResult(
                    tool_name=self.spec.name,
                    success=False,
                    output=None,
                    error="weather_config_missing",
                    failure_category="weather_config_missing",
                    metadata=_weather_metadata(provider_status="missing", mock_provider=False, real_provider=True),
                )
            return self._execute_real(payload, api_key)

        location = str(payload["location"])
        failure_mode = str(payload.get("simulate_failure", "")).strip()
        if failure_mode:
            key = f"{location}:{failure_mode}"
            self._attempts[key] = self._attempts.get(key, 0) + 1
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=failure_mode,
                failure_category=failure_mode,
                metadata=_weather_metadata(
                    provider_status="mock",
                    mock_provider=True,
                    real_provider=False,
                    attempt=self._attempts[key],
                ),
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={"location": location, "forecast": "Mock sunny", "temperature_c": 24},
            metadata=_weather_metadata(provider_status="mock", mock_provider=True, real_provider=False),
        )

    def downgrade(self, payload: dict[str, object], failure_result: ToolResult) -> ToolResult:  # noqa: ARG002
        location = str(payload["location"])
        needs_advice = bool(payload.get("needs_advice", False))
        output: dict[str, object] = {
            "location": location,
            "forecast": "Mock fallback weather",
            "temperature_c": 25,
            "source": "fallback_cache",
        }
        if needs_advice:
            output["advice"] = "Use the fallback weather result and leave extra commute time."
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output=output,
            metadata=_weather_metadata(
                provider_status="mock",
                mock_provider=True,
                real_provider=False,
                downgraded=True,
            ),
        )

    def _execute_real(self, payload: dict[str, object], api_key: str) -> ToolResult:
        location = str(payload["location"]).strip()
        configured_host = os.getenv("MINIBOT_WEATHER_API_HOST", "").strip()
        api_host = self._normalize_host(configured_host or "https://devapi.qweather.com")
        timeout = 10

        try:
            city = self._lookup_city(location, api_key, api_host, timeout)
            current = self._fetch_current_weather(city["id"], api_key, api_host, timeout)
        except urllib.error.URLError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error="weather_network_error",
                failure_category="weather_network_error",
                metadata=_weather_metadata(
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=True,
                    reason=str(exc.reason),
                ),
            )
        except ValueError as exc:
            message = str(exc)
            failure_category = self._classify_real_failure(message)
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=message,
                failure_category=failure_category,
                metadata=_weather_metadata(
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=True,
                    provider_host=api_host,
                ),
            )

        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={
                "location": location,
                "city": city["name"],
                "text": current.get("text", ""),
                "weather": current.get("text", ""),
                "temperature_c": self._to_number(current.get("temp")),
                "feels_like": self._to_number(current.get("feelsLike")),
                "humidity": self._to_number(current.get("humidity")),
                "wind_dir": current.get("windDir", ""),
                "wind_direction": current.get("windDir", ""),
                "wind_scale": current.get("windScale", ""),
                "obs_time": current.get("obsTime", ""),
                "updated_at": current.get("obsTime", ""),
            },
            metadata=_weather_metadata(
                provider_status="real",
                mock_provider=False,
                real_provider=True,
                provider_host=api_host,
            ),
        )

    def _fetch_json(self, url: str, api_key: str, timeout: int) -> dict[str, object]:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "X-QW-Api-Key": api_key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = self._decode_response_body(response)
        except urllib.error.HTTPError as exc:
            body = self._decode_error_body(exc)
            raise ValueError(f"QWeather HTTP {exc.code}: {body[:500]}") from exc
        if not body.strip():
            raise ValueError("QWeather empty response")
        try:
            payload = json.loads(body)
        except JSONDecodeError as exc:
            raise ValueError(f"QWeather returned non-JSON response: raw={self._summarize_non_json_body(body.encode('utf-8', errors='replace'))}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"QWeather returned non-JSON response: raw={self._summarize_non_json_body(body.encode('utf-8', errors='replace'))}")
        return payload

    def _lookup_city(self, location: str, api_key: str, api_host: str, timeout: int) -> dict[str, str]:
        query = urllib.parse.urlencode({"location": location, "range": "cn", "number": 1, "lang": "zh"})
        payload = self._fetch_json(f"{api_host}/geo/v2/city/lookup?{query}", api_key, timeout)
        if str(payload.get("code", "")) != "200":
            raise ValueError(f"QWeather API error: code={payload.get('code')}, body={json.dumps(payload, ensure_ascii=False)[:500]}")
        locations = payload.get("location", [])
        if not isinstance(locations, list) or not locations:
            raise ValueError("QWeather location not found")
        first = locations[0]
        if not isinstance(first, dict):
            raise ValueError(f"QWeather API error: code={payload.get('code')}, body={json.dumps(payload, ensure_ascii=False)[:500]}")
        return {
            "id": str(first.get("id", "")).strip(),
            "name": str(first.get("name", location)).strip() or location,
        }

    def _fetch_current_weather(self, location_id: str, api_key: str, api_host: str, timeout: int) -> dict[str, object]:
        if not location_id:
            raise ValueError("QWeather location not found")
        query = urllib.parse.urlencode({"location": location_id, "lang": "zh", "unit": "m"})
        payload = self._fetch_json(f"{api_host}/v7/weather/now?{query}", api_key, timeout)
        if str(payload.get("code", "")) != "200":
            raise ValueError(f"QWeather API error: code={payload.get('code')}, body={json.dumps(payload, ensure_ascii=False)[:500]}")
        now = payload.get("now", {})
        if not isinstance(now, dict) or not now:
            raise ValueError(f"QWeather API error: code={payload.get('code')}, body={json.dumps(payload, ensure_ascii=False)[:500]}")
        return now

    @staticmethod
    def _normalize_host(configured_host: str) -> str:
        normalized = configured_host.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://{normalized}"
        return normalized

    @classmethod
    def _decode_response_body(cls, response: object) -> str:
        raw = response.read()
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        encoding = str(getattr(response, "headers", {}).get("Content-Encoding", "") or "").strip().lower()
        decoded = cls._decode_raw_bytes(raw, encoding)
        return decoded.decode("utf-8", errors="replace")

    @classmethod
    def _decode_error_body(cls, exc: urllib.error.HTTPError) -> str:
        raw = exc.read()
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        encoding = str((exc.headers or {}).get("Content-Encoding", "") or "").strip().lower()
        decoded = cls._decode_raw_bytes(raw, encoding)
        return decoded.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_raw_bytes(raw: bytes, encoding: str) -> bytes:
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if encoding == "br":
            raise ValueError("QWeather returned brotli-compressed response; use Accept-Encoding: identity")
        return raw

    @staticmethod
    def _summarize_non_json_body(raw: bytes) -> str:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return repr(raw[:120])
        if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in text):
            return repr(raw[:120])
        return text[:500]

    @staticmethod
    def _classify_real_failure(message: str) -> str:
        if message.startswith("QWeather HTTP "):
            return "weather_http_error"
        if message == "QWeather empty response":
            return "weather_empty_response"
        if message.startswith("QWeather returned non-JSON response:"):
            return "weather_non_json_response"
        if message == "QWeather location not found":
            return "weather_location_not_found"
        if message.startswith("QWeather API error:"):
            return "weather_provider_failed"
        return "weather_provider_failed"

    @staticmethod
    def _to_number(value: object) -> int | float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return None
