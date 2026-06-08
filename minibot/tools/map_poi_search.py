"""Nearby POI search tool with mock and AMap MCP provider modes."""

from __future__ import annotations

import json
import os
import urllib.parse

from .base import BaseTool, ToolResult, ToolSpec
from .map_route import COORDINATE_PATTERN, MapRouteTool


def _poi_metadata(
    *,
    provider_status: str,
    mock_provider: bool,
    real_provider: bool,
    mcp_provider: bool,
    **extra: object,
) -> dict[str, object]:
    metadata = {
        "provider": "map_poi_search",
        "provider_status": provider_status,
        "mock_provider": mock_provider,
        "real_provider": real_provider,
        "mcp_provider": mcp_provider,
    }
    metadata.update(extra)
    return metadata


class MapPoiSearchTool(BaseTool):
    """Search nearby POIs with mock output or AMap MCP."""

    spec = ToolSpec(
        name="map_poi_search",
        description="Search nearby POIs such as hospitals or coffee shops around a location.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string"},
                "location": {"type": "string"},
                "keyword": {"type": "string"},
                "city": {"type": "string"},
                "radius": {"type": "integer"},
            },
        },
        risk_level="medium",
        sandbox_required=False,
        timeout=10,
        max_retries=0,
    )

    def __init__(self) -> None:
        self._route_helper = MapRouteTool()

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
                    metadata=_poi_metadata(
                        provider_status="missing",
                        mock_provider=False,
                        real_provider=False,
                        mcp_provider=True,
                    ),
                )
            return self._execute_mcp(payload, endpoint, api_key)

        query = str(payload.get("query", "")).strip()
        location = str(payload.get("location", "")).strip()
        keyword = str(payload.get("keyword", "")).strip() or query
        city = str(payload.get("city", "")).strip()
        radius = self._normalize_radius(payload.get("radius"))
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output={
                "query": query,
                "location": location,
                "keyword": keyword,
                "city": city,
                "radius": radius,
                "results": [
                    {
                        "name": f"Mock {keyword}",
                        "address": f"{location or '附近'}模拟地址",
                        "distance_m": 680,
                        "location": "118.100000,24.450000",
                        "type": keyword,
                    }
                ],
            },
            metadata=_poi_metadata(
                provider_status="mock",
                mock_provider=True,
                real_provider=False,
                mcp_provider=False,
            ),
        )

    def _execute_mcp(self, payload: dict[str, object], endpoint: str, api_key: str) -> ToolResult:
        query = str(payload.get("query", "")).strip()
        location = str(payload.get("location", "")).strip()
        keyword = str(payload.get("keyword", "")).strip()
        city = str(payload.get("city", "")).strip()
        radius = self._normalize_radius(payload.get("radius"))
        debug: dict[str, object] = {
            "mcp_endpoint_host": urllib.parse.urlsplit(endpoint).netloc,
            "request_method": "POST",
            "mcp_arguments": {
                "query": query,
                "location": location,
                "keyword": keyword,
                "city": city,
                "radius": radius,
            },
        }
        try:
            tools = self._route_helper._list_tools(endpoint, api_key, debug)  # type: ignore[attr-defined]
            tool_map = self._route_helper._index_tools(tools)  # type: ignore[attr-defined]
            center, resolved_city = self._resolve_center(
                location=location,
                city=city,
                endpoint=endpoint,
                api_key=api_key,
                tool_map=tool_map,
                debug=debug,
            )
            effective_city = city or resolved_city
            tool_name, tool_spec = self._resolve_poi_tool(tool_map)
            arguments = self._build_poi_arguments(
                tool_spec,
                query=query,
                location=location,
                center=center,
                keyword=keyword,
                city=effective_city,
                radius=radius,
            )
            debug["mcp_tool_name"] = tool_name
            debug["mcp_arguments"] = dict(arguments)
            result = self._route_helper._call_route_tool(endpoint, api_key, tool_name, arguments, debug)  # type: ignore[attr-defined]
            output = self._normalize_poi_output(
                result,
                query=query,
                location=location,
                keyword=keyword,
                city=effective_city,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                output=None,
                error=f"amap_mcp_error: {exc}",
                failure_category="amap_mcp_error",
                metadata=_poi_metadata(
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
            metadata=_poi_metadata(
                provider_status="mcp",
                mock_provider=False,
                real_provider=False,
                mcp_provider=True,
                **debug,
            ),
        )

    @staticmethod
    def _normalize_radius(raw: object) -> int:
        try:
            value = int(raw) if raw is not None else 3000
        except (TypeError, ValueError):
            value = 3000
        return max(100, min(value, 50000))

    def _resolve_center(
        self,
        *,
        location: str,
        city: str,
        endpoint: str,
        api_key: str,
        tool_map: dict[str, dict[str, object]],
        debug: dict[str, object],
    ) -> tuple[str, str]:
        if not location:
            return "", city
        if COORDINATE_PATTERN.match(location):
            return location, city
        center, resolved_city = self._route_helper._geocode_with_mcp_or_rest(  # type: ignore[attr-defined]
            location,
            city=city,
            endpoint=endpoint,
            api_key=api_key,
            tool_map=tool_map,
            debug=debug,
        )
        return center, resolved_city or city

    @staticmethod
    def _resolve_poi_tool(tool_map: dict[str, dict[str, object]]) -> tuple[str, dict[str, object]]:
        candidates = [
            "maps_around_search",
            "maps_poi_around_search",
            "maps_place_around_search",
            "maps_search_around",
            "maps_poi_search",
            "maps_text_search",
            "maps_place_text_search",
        ]
        for name in candidates:
            tool = tool_map.get(name)
            if tool is not None:
                return name, tool
        raise ValueError("poi_tool_missing")

    @staticmethod
    def _build_poi_arguments(
        tool: dict[str, object],
        *,
        query: str,
        location: str,
        center: str,
        keyword: str,
        city: str,
        radius: int,
    ) -> dict[str, object]:
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict):
            properties = {}
        args: dict[str, object] = {}
        mapping = {
            "query": query or keyword,
            "keywords": keyword or query,
            "keyword": keyword or query,
            "types": keyword,
            "location": center or location,
            "center": center or location,
            "origin": center or location,
            "lonlat": center or location,
            "address": location,
            "city": city,
            "region": city,
            "radius": radius,
            "distance": radius,
        }
        for key, value in mapping.items():
            if key in properties and value not in {"", None}:
                args[key] = value
        if not args:
            args = {
                "query": query or keyword,
                "keyword": keyword or query,
                "location": center or location,
                "city": city,
                "radius": radius,
            }
        return {key: value for key, value in args.items() if value not in {"", None}}

    @classmethod
    def _normalize_poi_output(
        cls,
        result: dict[str, object],
        *,
        query: str,
        location: str,
        keyword: str,
        city: str,
    ) -> dict[str, object]:
        payload = cls._extract_poi_payload(result)
        pois = payload.get("pois") or payload.get("results") or payload.get("data") or []
        normalized_results: list[dict[str, object]] = []
        if isinstance(pois, list):
            for item in pois:
                if not isinstance(item, dict):
                    continue
                normalized_results.append(
                    {
                        "name": str(item.get("name", "")).strip(),
                        "address": str(item.get("address") or item.get("addr") or "").strip(),
                        "distance_m": cls._to_int(item.get("distance")),
                        "location": str(
                            item.get("location")
                            or item.get("lonlat")
                            or item.get("lnglat")
                            or item.get("coordinate")
                            or ""
                        ).strip(),
                        "type": str(item.get("type") or item.get("type_name") or keyword).strip(),
                    }
                )
        return {
            "query": query,
            "location": location,
            "keyword": keyword,
            "city": city,
            "results": normalized_results,
            "raw_result": result,
        }

    @classmethod
    def _extract_poi_payload(cls, result: dict[str, object]) -> dict[str, object]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return {}

    @staticmethod
    def _to_int(value: object) -> int | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None
