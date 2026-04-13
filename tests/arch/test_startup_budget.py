"""Startup budget enforcement (REQ-STARTUP-001).

The serve() -> mcp.run() critical path must not contain subprocess calls.
Any subprocess on this path risks exceeding Claude Code's ~5s connection
timeout, causing "No such tool available" for all MCP tools.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

FORBIDDEN_SUBPROCESS_CALLS = frozenset(
    {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"}
)


def _get_call_name(node: ast.Call) -> str:
    """Extract dotted call name from an ast.Call node."""
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return f"{node.func.value.id}.{node.func.attr}"
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def test_no_subprocess_in_make_context() -> None:
    """REQ-STARTUP-001: make_context() must not call subprocess.run or similar."""
    factory_src = (SRC / "server" / "_factory.py").read_text()
    tree = ast.parse(factory_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "make_context":
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    call_name = _get_call_name(child)
                    assert call_name not in FORBIDDEN_SUBPROCESS_CALLS, (
                        f"make_context() calls {call_name} at line {child.lineno} — "
                        f"this blocks the MCP server startup path"
                    )
            break
    else:
        raise AssertionError("make_context() not found in _factory.py")


def test_no_gh_cli_token_in_make_context() -> None:
    """REQ-STARTUP-001: make_context() must not call _gh_cli_token() directly.

    The _gh_cli_token() function runs subprocess.run with a 5s timeout.
    Token resolution must be lazy (deferred to first gated tool call).
    """
    factory_src = (SRC / "server" / "_factory.py").read_text()
    tree = ast.parse(factory_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "make_context":
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    call_name = _get_call_name(child)
                    assert call_name != "_gh_cli_token", (
                        f"make_context() calls _gh_cli_token() at line {child.lineno} — "
                        f"this 5s subprocess blocks the MCP server startup path. "
                        f"Token resolution must be lazy."
                    )
            break
    else:
        raise AssertionError("make_context() not found in _factory.py")
