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


def _collect_meta_backstop_attachments() -> dict[str, str]:
    """AST-scan server modules for ``meta=response_backstop_tool_meta(tool_name)`` calls
    used as keyword arguments in ``@mcp.tool(...)`` decorators.

    Returns a mapping from the *first positional string argument* of the
    ``response_backstop_tool_meta(...)`` call to the decorated function name.
    """
    server_dir = SRC_ROOT / "server"
    attached: dict[str, str] = {}
    for py_file in list(server_dir.glob("*.py")) + list((server_dir / "tools").glob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
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
                    if kw.arg != "meta":
                        continue
                    call = kw.value
                    if not isinstance(call, ast.Call):
                        continue
                    # Accept both bare name and attribute forms of the helper
                    func = call.func
                    if isinstance(func, ast.Name) and func.id == "response_backstop_tool_meta":
                        pass
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr == "response_backstop_tool_meta"
                    ):
                        pass
                    else:
                        continue
                    # Extract the first positional argument (the tool name string)
                    if (
                        call.args
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)
                    ):
                        attached[call.args[0].value] = node.name
    return attached


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
    must match the function the decorator is attached to, or be a known alias.
    """
    from autoskillit.core import RESPONSE_BACKSTOP_EXEMPTION_REGISTRY

    attached = _collect_meta_backstop_attachments()
    mismatches: list[str] = []
    for tool_name, func_name in attached.items():
        if tool_name != func_name:
            # Ensure the tool_name is at least a valid registry key
            if tool_name not in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY:
                mismatches.append(
                    f"  {func_name}: meta names {tool_name!r} which is not in the registry"
                )
    assert not mismatches, (
        "meta=response_backstop_tool_meta(...) tool name does not match function name "
        "and is not a registered key:\n" + "\n".join(mismatches)
    )
