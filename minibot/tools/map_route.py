"""Map routing tool with mock and MCP provider boundary."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import BaseTool, ToolResult, ToolSpec


COORDINATE_PATTERN = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")


def _map_metadata(
    *,
    provider_status: str,
    mock_provider: bool,
    real_provider: bool,
    mcp_provider: bool,
    **extra: object,
) -> dict[str, object]:
    metadata = {
        "provider": "map_route",
        "provider_status": provider_status,
        "mock_provider": mock_provider,
        "real_provider": real_provider,
        "mcp_provider": mcp_provider,
    }
    metadata.update(extra)
    return metadata


class MapRouteTool(BaseTool):
    """Return a mock route plan or structured MCP results."""

    spec = ToolSpec(
        name="map_route",
        description="Plan a route between origin and destination.",
        input_schema={
            "type": "object",
            "required": ["origin", "destination"],
            "additionalProperties": False,
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "city": {"type": "string"},
                "mode": {"type": "string"},
            },
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=10,
        max_retries=0,
    )

    def execute(self, payload: dict[str, object]) -> ToolResult:
        provider = os.getenv("MINIBOT_MAP_PROVIDER", "mock").strip().lower() or "mock"
        if provider == "mcp":
            endpoint = os.getenv("MINIBOT_AMAP_MCP_ENDPOINT", "").strip()
            api_key = os.getenv("MINIBOT_AMAP_MCP_API_KEY", "").strip()
            if not endpoint or not api_key:
                return ToolResult(
                    tool_name=self.spec.name,
                    success=False,
                    output=None,
                    error="amap_mcp_config_missing",
                    failure_category="amap_mcp_config_missing",
                    metadata=_map_metadata(
                        provider_status="missing",
                        mock_provider=False,
                        real_provider=False,
                        mcp_provider=True,
                    ),
                )
            return self._execute_mcp(payload, endpoint, api_key)

        origin = str(payload["origin"])
        destination = str(payload["destination"])
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={
                "origin": origin,
                "destination": destination,
                "distance_km": 12.5,
                "duration_minutes": 28,
            },
            metadata=_map_metadata(
                provider_status="mock",
                mock_provider=True,
                real_provider=False,
                mcp_provider=False,
            ),
        )

    def _execute_mcp(self, payload: dict[str, object], endpoint: str, api_key: str) -> ToolResult:
        origin = str(payload["origin"]).strip()
        destination = str(payload["destination"]).strip()
        city = str(payload.get("city", "")).strip()
        mode = str(payload.get("mode", "driving")).strip().lower() or "driving"
        debug: dict[str, object] = {
            "mcp_endpoint_host": urllib.parse.urlsplit(endpoint).netloc,
            "request_method": "POST",
            "mcp_arguments": {
                "origin": origin,
                "destination": destination,
                "city": city,
                "mode": mode,
            },
        }
        try:
            tools = self._list_tools(endpoint, api_key, debug)
            tool_map = self._index_tools(tools)
            route_tool_name = self._select_route_tool_name(mode)
            route_tool = self._resolve_route_tool(tool_map, route_tool_name)
            route_tool_name = str(route_tool.get("name", route_tool_name)).strip() or route_tool_name
            legacy_text_route = route_tool_name == "maps_route_plan"
            if legacy_text_route:
                resolved_origin, origin_city = origin, city
                resolved_destination, destination_city = destination, city
            else:
                resolved_origin, origin_city = self._resolve_location(
                    origin,
                    city=city,
                    endpoint=endpoint,
                    api_key=api_key,
                    tool_map=tool_map,
                    debug=debug,
                )
                resolved_destination, destination_city = self._resolve_location(
                    destination,
                    city=city or origin_city,
                    endpoint=endpoint,
                    api_key=api_key,
                    tool_map=tool_map,
                    debug=debug,
                )
            debug["mcp_tool_name"] = route_tool_name
            effective_city = city or origin_city or destination_city
            route_arguments = self._build_route_arguments(
                route_tool,
                origin=resolved_origin,
                destination=resolved_destination,
                city=effective_city,
                mode=mode,
            )
            if route_tool_name == "maps_direction_transit_integrated" and "city" not in route_arguments and effective_city:
                route_arguments["city"] = effective_city
            if route_tool_name == "maps_direction_transit_integrated" and "city" not in route_arguments:
                raise ValueError("transit_route_city_missing")
            debug["mcp_arguments"] = dict(route_arguments)
            result = self._call_route_tool(endpoint, api_key, route_tool_name, route_arguments, debug)
            output = self._normalize_route_output(result, origin, destination)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"amap_mcp_error: {exc}",
                failure_category="amap_mcp_error",
                metadata=_map_metadata(
                    provider_status="failed",
                    mock_provider=False,
                    real_provider=False,
                    mcp_provider=True,
                    **debug,
                ),
            )

        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output=output,
            metadata=_map_metadata(
                provider_status="mcp",
                mock_provider=False,
                real_provider=False,
                mcp_provider=True,
                **debug,
            ),
        )

    def _list_tools(self, endpoint: str, api_key: str, debug: dict[str, object]) -> list[dict[str, object]]:
        response = self._mcp_request(endpoint, api_key, "tools/list", {}, debug)
        tools = response.get("result", {}).get("tools", [])
        if not isinstance(tools, list) or not tools:
            raise ValueError("no_route_tool_available")
        normalized = [tool for tool in tools if isinstance(tool, dict)]
        if not normalized:
            raise ValueError("no_route_tool_available")
        return normalized

    @staticmethod
    def _index_tools(tools: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        return {str(tool.get("name", "")).strip(): tool for tool in tools if str(tool.get("name", "")).strip()}

    @staticmethod
    def _is_coordinate(value: str) -> bool:
        return bool(COORDINATE_PATTERN.match(value))

    def _resolve_location(
        self,
        value: str,
        *,
        city: str,
        endpoint: str,
        api_key: str,
        tool_map: dict[str, dict[str, object]],
        debug: dict[str, object],
    ) -> tuple[str, str]:
        if self._is_coordinate(value):
            return value, city
        location, resolved_city = self._geocode_with_mcp_or_rest(
            value,
            city=city,
            endpoint=endpoint,
            api_key=api_key,
            tool_map=tool_map,
            debug=debug,
        )
        return location, resolved_city or city

    def _geocode_with_mcp_or_rest(
        self,
        address: str,
        *,
        city: str,
        endpoint: str,
        api_key: str,
        tool_map: dict[str, dict[str, object]],
        debug: dict[str, object],
    ) -> tuple[str, str]:
        geo_tool = tool_map.get("maps_geo")
        if geo_tool is not None:
            try:
                arguments = self._build_geo_arguments(geo_tool, address=address, city=city)
                debug["request_payload_without_key"] = {
                    "method": "tools/call",
                    "params": {
                        "name": "maps_geo",
                        "arguments": arguments,
                    },
                }
                result = self._call_route_tool(endpoint, api_key, "maps_geo", arguments, debug)
                location, resolved_city = self._extract_geocode_result(result)
                if location:
                    return location, resolved_city
            except ValueError:
                pass
        return self._rest_geocode(address, city=city, api_key=api_key)

    @staticmethod
    def _build_geo_arguments(tool: dict[str, object], *, address: str, city: str) -> dict[str, object]:
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict):
            properties = {}
        mapping = {
            "address": address,
            "keyword": address,
            "query": address,
            "location": address,
            "city": city,
            "region": city,
        }
        args: dict[str, object] = {}
        for key, value in mapping.items():
            if key in properties and value:
                args[key] = value
        if not args:
            args = {"address": address}
            if city:
                args["city"] = city
        return {key: value for key, value in args.items() if value not in {"", None}}

    @staticmethod
    def _extract_geocode_result(result: dict[str, object]) -> tuple[str, str]:
        structured = result.get("structuredContent")
        for payload in MapRouteTool._iter_geocode_payloads(structured):
            location, resolved_city = MapRouteTool._extract_location_from_payload(payload)
            if location:
                return location, resolved_city
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parsed = MapRouteTool._safe_parse_json_text(text)
                    if isinstance(parsed, dict):
                        for payload in MapRouteTool._iter_geocode_payloads(parsed):
                            location, resolved_city = MapRouteTool._extract_location_from_payload(payload)
                            if location:
                                return location, resolved_city
                    match = COORDINATE_PATTERN.search(text)
                    if match:
                        return match.group(0), ""
        raise ValueError("geocode_resolution_failed")

    @staticmethod
    def _iter_geocode_payloads(payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, dict):
            return []
        payloads = [payload]
        for key in ("results", "geocodes", "pois"):
            value = payload.get(key)
            if isinstance(value, list):
                payloads.extend(item for item in value if isinstance(item, dict))
        return payloads

    @staticmethod
    def _extract_location_from_payload(payload: dict[str, object]) -> tuple[str, str]:
        candidates = (
            payload.get("location"),
            payload.get("lnglat"),
            payload.get("coordinate"),
            payload.get("lonlat"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and COORDINATE_PATTERN.match(candidate):
                resolved_city = str(payload.get("city", "")).strip()
                return candidate, resolved_city
        return "", ""

    @staticmethod
    def _safe_parse_json_text(text: str) -> dict[str, object] | None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _rest_geocode(self, address: str, *, city: str, api_key: str) -> tuple[str, str]:
        query = {
            "key": api_key,
            "address": address,
        }
        if city:
            query["city"] = city
        url = f"https://restapi.amap.com/v3/geocode/geo?{urllib.parse.urlencode(query)}"
        with urllib.request.urlopen(url, timeout=self.spec.timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        geocodes = payload.get("geocodes", [])
        if not isinstance(geocodes, list) or not geocodes:
            raise ValueError("rest_geocode_failed")
        first = geocodes[0]
        if not isinstance(first, dict):
            raise ValueError("rest_geocode_failed")
        location = str(first.get("location", "")).strip()
        if not self._is_coordinate(location):
            raise ValueError("rest_geocode_failed")
        resolved_city = str(first.get("city", "") or city).strip()
        return location, resolved_city

    @staticmethod
    def _select_route_tool_name(mode: str) -> str:
        return {
            "driving": "maps_direction_driving",
            "walking": "maps_direction_walking",
            "bicycling": "maps_direction_bicycling",
            "transit": "maps_direction_transit_integrated",
        }.get(mode, "maps_direction_driving")

    @staticmethod
    def _resolve_route_tool(tool_map: dict[str, dict[str, object]], tool_name: str) -> dict[str, object]:
        tool = tool_map.get(tool_name)
        if tool is None and tool_name != "maps_route_plan":
            tool = tool_map.get("maps_route_plan")
            tool_name = "maps_route_plan" if tool is not None else tool_name
        if tool is None:
            raise ValueError(f"route_tool_missing:{tool_name}")
        return tool

    def _call_route_tool(
        self,
        endpoint: str,
        api_key: str,
        tool_name: str,
        route_arguments: dict[str, object],
        debug: dict[str, object],
    ) -> dict[str, object]:
        response = self._mcp_request(
            endpoint,
            api_key,
            "tools/call",
            {
                "name": tool_name,
                "arguments": route_arguments,
            },
            debug,
        )
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise ValueError("invalid_mcp_result")
        return result

    def _mcp_request(
        self,
        endpoint: str,
        api_key: str,
        method: str,
        params: dict[str, object],
        debug: dict[str, object],
    ) -> dict[str, object]:
        request_payload = {
            "jsonrpc": "2.0",
            "id": f"map-route-{method.replace('/', '-')}",
            "method": method,
            "params": params,
        }
        debug["request_payload_without_key"] = self._sanitize_value(request_payload, api_key)
        request = urllib.request.Request(
            endpoint,
            method="POST",
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.spec.timeout) as response:  # noqa: S310
            debug["response_status"] = getattr(response, "status", None)
            body = response.read().decode("utf-8", errors="replace")
        debug["response_body_snippet"] = self._sanitize_text(body[:500], api_key)
        payload = self._parse_mcp_body(body)
        if not isinstance(payload, dict):
            raise ValueError("invalid_mcp_response")
        if payload.get("error"):
            error = payload["error"]
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                if message and code is not None:
                    raise ValueError(f"code={code}, message={message}")
                if code is not None:
                    raise ValueError(f"code={code}")
                raise ValueError("mcp_error")
            raise ValueError(str(error))
        return payload

    @staticmethod
    def _parse_mcp_body(body: str) -> dict[str, object]:
        if not body.strip():
            raise ValueError("empty_mcp_response")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            events: list[dict[str, object]] = []
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                chunk = stripped.removeprefix("data:").strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    item = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    events.append(item)
            if not events:
                raise ValueError("invalid_mcp_response")
            return events[-1]

    @staticmethod
    def _build_route_arguments(
        tool: dict[str, object],
        *,
        origin: str,
        destination: str,
        city: str,
        mode: str,
    ) -> dict[str, object]:
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict):
            properties = {}
        args: dict[str, object] = {}
        mapping = {
            "origin": origin,
            "from": origin,
            "start": origin,
            "source": origin,
            "destination": destination,
            "to": destination,
            "end": destination,
            "target": destination,
            "city": city,
            "mode": mode,
            "strategy": mode,
            "travel_mode": mode,
        }
        for key, value in mapping.items():
            if key in properties and value:
                args[key] = value
        if not args:
            args = {
                "origin": origin,
                "destination": destination,
                "city": city,
                "mode": mode,
            }
        return {key: value for key, value in args.items() if value not in {"", None}}

    @staticmethod
    def _normalize_route_output(result: dict[str, object], origin: str, destination: str) -> dict[str, object]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            summary = str(structured.get("summary", "")).strip()
            return {
                "origin": origin,
                "destination": destination,
                "summary": summary or f"Route plan from {origin} to {destination}",
                "distance_km": structured.get("distance_km"),
                "duration_minutes": structured.get("duration_minutes"),
                "steps_summary": structured.get("steps") or structured.get("steps_summary") or [],
                "raw_result": structured,
            }

        content = result.get("content")
        if isinstance(content, list) and content:
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                paths = parsed.get("paths", [])
                if not isinstance(paths, list) or not paths:
                    continue
                first_path = paths[0]
                if not isinstance(first_path, dict):
                    continue
                distance_m = MapRouteTool._to_float(first_path.get("distance"))
                duration_s = MapRouteTool._to_float(first_path.get("duration"))
                steps = first_path.get("steps", [])
                instructions: list[str] = []
                if isinstance(steps, list):
                    for step in steps:
                        if isinstance(step, dict) and isinstance(step.get("instruction"), str):
                            instructions.append(str(step["instruction"]))
                distance_km = round(distance_m / 1000, 1) if distance_m is not None else None
                duration_minutes = round(duration_s / 60, 1) if duration_s is not None else None
                mode = "driving"
                summary_parts = [f"从{origin}到{destination}"]
                mode_label = {
                    "driving": "驾车",
                    "walking": "步行",
                    "bicycling": "骑行",
                    "transit": "公交",
                }.get(mode, "路线")
                if distance_km is not None and duration_minutes is not None:
                    summary = f"{summary_parts[0]}，{mode_label}距离约{distance_km}公里，预计用时约{duration_minutes}分钟。"
                elif distance_km is not None:
                    summary = f"{summary_parts[0]}，{mode_label}距离约{distance_km}公里。"
                else:
                    summary = f"{summary_parts[0]}，已获取{mode_label}路线。"
                return {
                    "origin": origin,
                    "destination": destination,
                    "mode": mode,
                    "origin_location": parsed.get("origin"),
                    "destination_location": parsed.get("destination"),
                    "distance_km": distance_km,
                    "duration_minutes": duration_minutes,
                    "summary": summary,
                    "steps_summary": instructions,
                    "raw_result": result,
                }

        content = result.get("content")
        if isinstance(content, list) and content:
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(str(item["text"]))
            if text_parts:
                return {
                    "origin": origin,
                    "destination": destination,
                    "summary": "\n".join(text_parts),
                    "distance_km": None,
                    "duration_minutes": None,
                    "steps_summary": text_parts,
                    "raw_result": result,
                }

        return {
            "origin": origin,
            "destination": destination,
            "summary": f"Route plan from {origin} to {destination}",
            "distance_km": None,
            "duration_minutes": None,
            "steps_summary": [],
            "raw_result": result,
        }

    @staticmethod
    def _sanitize_text(value: str, api_key: str) -> str:
        return value.replace(api_key, "***") if api_key else value

    @classmethod
    def _sanitize_value(cls, value: object, api_key: str) -> object:
        if isinstance(value, str):
            return cls._sanitize_text(value, api_key)
        if isinstance(value, dict):
            return {str(key): cls._sanitize_value(item, api_key) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._sanitize_value(item, api_key) for item in value]
        return value

    @staticmethod
    def _to_float(value: object) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
