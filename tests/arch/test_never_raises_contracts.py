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

_NEVER_RAISES_RE = re.compile(r"never raises", re.IGNORECASE)


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _has_toplevel_except_exception(func_node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return True if the function body's first substantive statement is a try/except Exception."""
    body = func_node.body
    # Skip docstring
    stmts = [
        s for s in body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)
    ]
    if not stmts:
        return False
    first = stmts[0]
    if not isinstance(first, ast.Try):
        return False
    # Check that at least one handler catches Exception or BaseException
    for handler in first.handlers:
        if handler.type is None:  # bare except:
            return True
        if isinstance(handler.type, ast.Name) and handler.type.id in (
            "Exception",
            "BaseException",
        ):
            return True
        if isinstance(handler.type, ast.Attribute) and handler.type.attr in (
            "Exception",
            "BaseException",
        ):
            return True
    return False


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
