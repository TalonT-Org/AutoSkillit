"""Bidirectional parity: every RESPONSE_BACKSTOP_EXEMPTION_REGISTRY key has a
matching ``meta=response_backstop_tool_meta(...)`` attachment on its ``@mcp.tool``
decorator, and every such attachment has a matching registry key.

Modeled on ``test_layer_enforcement.py::test_all_mcp_tools_are_registered``.
"""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _iter_server_module_trees() -> list[ast.Module]:
    """Parse every server module, including decomposed ``tools_*/``/``_*/`` packages.

    Uses ``rglob`` (not a flat ``glob``) so tool handlers that moved into a
    directory package during decomposition (e.g.
    ``server/tools/tools_kitchen/_open_kitchen.py``) are still scanned.
    """
    server_dir = SRC_ROOT / "server"
    trees = []
    for py_file in list(server_dir.rglob("*.py")):
        trees.append(ast.parse(py_file.read_text(), filename=str(py_file)))
    return trees


def _is_backstop_meta_call(call: ast.expr) -> bool:
    """True if ``call`` is a ``response_backstop_tool_meta(...)`` call, accepting both
    bare name and attribute forms of the helper.
    """
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name) and func.id == "response_backstop_tool_meta":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "response_backstop_tool_meta"


def _backstop_call_tool_name(call: ast.Call) -> str | None:
    """Extract the first positional argument (the tool name string), if present."""
    if (
        call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ):
        return call.args[0].value
    return None


def _collect_meta_backstop_attachments() -> dict[str, str]:
    """AST-scan server modules for ``meta=response_backstop_tool_meta(tool_name)`` calls
    used as keyword arguments in ``@mcp.tool(...)`` decorators.

    Returns a mapping from the *first positional string argument* of the
    ``response_backstop_tool_meta(...)`` call to the decorated function name.
    """
    attached: dict[str, str] = {}
    for tree in _iter_server_module_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                # Match @mcp.tool(..., meta=response_backstop_tool_meta("X"), ...)
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr != "tool":
                    continue
                for kw in dec.keywords:
                    if kw.arg != "meta" or not _is_backstop_meta_call(kw.value):
                        continue
                    assert isinstance(kw.value, ast.Call)  # narrowed by _is_backstop_meta_call
                    tool_name = _backstop_call_tool_name(kw.value)
                    if tool_name is not None:
                        attached[tool_name] = node.name
    return attached


def _collect_all_backstop_calls() -> set[str]:
    """AST-scan server modules for EVERY ``response_backstop_tool_meta(...)`` call site,
    regardless of where it appears — not just those wired into a decorator's ``meta=``
    keyword. Used to detect stray/unattached calls to the helper.

    Returns the set of *first positional string arguments* across all call sites.
    """
    all_calls: set[str] = set()
    for tree in _iter_server_module_trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_backstop_meta_call(node)):
                continue
            tool_name = _backstop_call_tool_name(node)
            if tool_name is not None:
                all_calls.add(tool_name)
    return all_calls


def test_response_backstop_registry_decorator_parity() -> None:
    """Every registry key must have a ``meta=`` attachment on its ``@mcp.tool``
    decorator, and every attachment must have a registry key.
    """
    from autoskillit.core import RESPONSE_BACKSTOP_EXEMPTION_REGISTRY

    registry_keys = set(RESPONSE_BACKSTOP_EXEMPTION_REGISTRY)
    attached = _collect_meta_backstop_attachments()
    attached_keys = set(attached)

    missing = registry_keys - attached_keys
    orphaned = attached_keys - registry_keys

    assert not missing, (
        f"Registry keys without a meta=response_backstop_tool_meta(...) attachment "
        f"on their @mcp.tool decorator: {sorted(missing)}"
    )
    assert not orphaned, (
        f"meta=response_backstop_tool_meta(...) attachments with no registry entry: "
        f"{sorted(orphaned)}"
    )


def test_meta_attachment_names_match_decorated_function() -> None:
    """Guard against copy-paste: the tool name passed to ``response_backstop_tool_meta``
    must match the function the decorator is attached to.
    """
    attached = _collect_meta_backstop_attachments()
    mismatches: list[str] = []
    for tool_name, func_name in attached.items():
        if tool_name != func_name:
            mismatches.append(f"  {func_name}: meta names {tool_name!r}")
    assert not mismatches, (
        "meta=response_backstop_tool_meta(...) tool name does not match function name:\n"
        + "\n".join(mismatches)
    )


def test_no_unattached_backstop_helper_calls() -> None:
    """Every ``response_backstop_tool_meta(...)`` call site must be wired into a
    ``@mcp.tool(..., meta=...)`` decorator. A call made anywhere else (e.g. assigned to
    a variable, passed to something other than ``meta=``, or on a decorator that isn't
    ``.tool(...)``) is a stray call that silently drops the exemption it was meant to
    register.
    """
    attached_keys = set(_collect_meta_backstop_attachments())
    all_calls = _collect_all_backstop_calls()

    stray = all_calls - attached_keys
    assert not stray, (
        f"response_backstop_tool_meta(...) called but not attached as meta= on an "
        f"@mcp.tool decorator: {sorted(stray)}"
    )
