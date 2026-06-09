"""Tool registry and schema validation."""

from __future__ import annotations

from typing import Any

from .base import BaseTool, ToolNotFoundError, ToolSpec, ToolValidationError


class ToolRegistry:
    """Register tools, expose metadata, and validate tool input."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"tool_not_found: {name}", "tool_not_found")
        return tool

    def list_tools(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def get_spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    def validate_input(self, name: str, payload: dict[str, object]) -> None:
        spec = self.get_spec(name)
        schema = dict(spec.input_schema)
        if schema.get("type") != "object":
            return
        if not isinstance(payload, dict):
            raise ToolValidationError(
                f"invalid_payload_type for {name}", "schema_validation_failed"
            )

        required = list(schema.get("required", []))
        properties = dict(schema.get("properties", {}))
        additional = bool(schema.get("additionalProperties", True))

        for field in required:
            if field not in payload:
                raise ToolValidationError(
                    f"missing_required_field: {field}",
                    "schema_validation_failed",
                )
        for key, value in payload.items():
            if key not in properties:
                if not additional:
                    raise ToolValidationError(
                        f"unexpected_field: {key}",
                        "schema_validation_failed",
                    )
                continue
            self._validate_value(name=name, field=key, value=value, schema=properties[key])

    def _validate_value(
        self, *, name: str, field: str, value: object, schema: dict[str, Any]
    ) -> None:
        expected_type = schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            raise ToolValidationError(f"{name}.{field} must be string", "schema_validation_failed")
        if expected_type == "integer" and not isinstance(value, int):
            raise ToolValidationError(f"{name}.{field} must be integer", "schema_validation_failed")
        if expected_type == "boolean" and not isinstance(value, bool):
            raise ToolValidationError(f"{name}.{field} must be boolean", "schema_validation_failed")
        if expected_type == "array" and not isinstance(value, list):
            raise ToolValidationError(f"{name}.{field} must be array", "schema_validation_failed")
        if expected_type == "object" and not isinstance(value, dict):
            raise ToolValidationError(f"{name}.{field} must be object", "schema_validation_failed")
