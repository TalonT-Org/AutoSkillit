"""
Structural enforcement of 'Never raises' docstring contracts in server/.

Any async function in server/ that claims 'Never raises' in its docstring
must have a top-level 'try:' block as the first statement (after docstring),
with 'except Exception' or 'except BaseException' covering the entire body.

This test catches the class of bug where a docstring makes a promise that
the code does not structurally honor.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.arch._helpers import SRC_ROOT, _has_toplevel_except_exception, _is_mcp_tool_decorator

_NEVER_RAISES_RE = re.compile(r"never raises", re.IGNORECASE)


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def test_all_mcp_tool_handlers_have_except_exception() -> None:
    """Every @mcp.tool() decorated function in server/ must have a
    top-level try/except Exception block.

    This is stronger than test_never_raises_contracts (which is opt-in via
    docstring). This rule is mandatory for all tool handlers — exceptions
    that escape to @track_response_size produce generic envelopes that
    lack domain-specific fields callers need for routing.
    """
    server_dir = SRC_ROOT / "server"
    violations: list[str] = []

    for path in sorted(server_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            if not _has_toplevel_except_exception(node):
                rel = path.relative_to(SRC_ROOT.parent.parent)
                violations.append(f"{rel}:{node.lineno} — {node.name}()")

    assert not violations, (
        "@mcp.tool() handlers must have a top-level try/except Exception "
        "to return domain-specific structured errors instead of relying on "
        "the @track_response_size safety net.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_all_mcp_tool_handlers_claim_never_raises() -> None:
    """Every @mcp.tool() handler should declare 'Never raises' in its docstring.

    This activates test_never_raises_contracts enforcement as a second layer.
    """
    server_dir = SRC_ROOT / "server"
    missing: list[str] = []

    for path in sorted(server_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            docstring = ast.get_docstring(node) or ""
            if "never raises" not in docstring.lower():
                rel = path.relative_to(SRC_ROOT.parent.parent)
                missing.append(f"{rel}:{node.lineno} — {node.name}()")

    assert not missing, (
        "@mcp.tool() handlers should claim 'Never raises' in their docstring.\n"
        "Missing:\n" + "\n".join(f"  {v}" for v in missing)
    )


def test_never_raises_contracts_are_structurally_enforced() -> None:
    """All 'Never raises' functions in server/ must have a top-level try/except Exception."""
    server_dir = _repo_root() / "src" / "autoskillit" / "server"
    violations: list[str] = []

    for path in sorted(server_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            docstring = ast.get_docstring(node)
            if not docstring or not _NEVER_RAISES_RE.search(docstring):
                continue
            if not _has_toplevel_except_exception(node):
                rel = path.relative_to(_repo_root())
                violations.append(
                    f"{rel}:{node.lineno} — {node.name}() claims 'Never raises'"
                    " but lacks top-level try/except Exception"
                )

    assert not violations, (
        "Functions claiming 'Never raises' must structurally enforce it"
        " with a top-level try/except Exception.\n"
        "Add a bare try/except Exception as the first statement of the function body.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
