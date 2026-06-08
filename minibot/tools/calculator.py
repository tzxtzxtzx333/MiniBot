"""Calculator tool."""

from __future__ import annotations

import ast
import operator

from .base import BaseTool, ToolError, ToolSpec


class CalculatorTool(BaseTool):
    """Evaluate safe arithmetic expressions."""

    spec = ToolSpec(
        name="calculator",
        description="Evaluate arithmetic expressions with + - * / and parentheses.",
        input_schema={
            "type": "object",
            "required": ["expression"],
            "additionalProperties": False,
            "properties": {"expression": {"type": "string"}},
        },
        risk_level="low",
        sandbox_required=False,
        timeout=5,
        max_retries=0,
    )

    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        expression = str(payload["expression"]).strip()
        try:
            result = self._eval_ast(ast.parse(expression, mode="eval").body)
        except SyntaxError as exc:
            message = f"{exc.msg} (<string>, line {exc.lineno or 1})"
            raise ToolError(message, "tool_execution_failed") from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(str(exc), "tool_execution_failed") from exc
        return {"result": result, "expression": expression}

    def _eval_ast(self, node: ast.AST) -> float | int:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in self._operators:
            left = self._eval_ast(node.left)
            right = self._eval_ast(node.right)
            return self._operators[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._operators:
            return self._operators[type(node.op)](self._eval_ast(node.operand))
        raise ValueError("unsupported expression")
