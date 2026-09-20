"""Every registered MCP tool declares its session admission scope."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SERVER_ROOT = Path(__file__).parents[2] / "src/autoskillit/server"


def _has_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == name
        for decorator in node.decorator_list
    )


def _is_mcp_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "mcp"
        and decorator.func.attr == "tool"
        and not any(keyword.arg == "name" for keyword in decorator.keywords)
        for decorator in node.decorator_list
    )


def test_every_mcp_tool_declares_session_scope() -> None:
    missing: list[str] = []
    for path in _SERVER_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_mcp_tool(node):
                if not _has_decorator(node, "session_scoped"):
                    missing.append(f"{path.relative_to(_SERVER_ROOT)}:{node.name}")

    assert not missing, "MCP tools missing @session_scoped: " + ", ".join(sorted(missing))
