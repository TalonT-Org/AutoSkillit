"""Arch guard: every ctx.enable_components call in server/tools/ must have a
non-notification fallback or be explicitly exempted as a session-scoped unlock.

Session-scoped unlocks (e.g. unlock_agent_pack) are exempt because the resources
they reveal are pre-revealed at startup for non-notification backends, so the
notification path is only an optimization, not a correctness requirement.
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_EXEMPT_FUNCTIONS: frozenset[str] = frozenset({"unlock_agent_pack"})

# The guard variable name that signals a non-notification fallback.
_SKIP_GUARD_NAMES: frozenset[str] = frozenset({"_skip_notify"})


class _EnableComponentsCallFinder(ast.NodeVisitor):
    """Find all ctx.enable_components call sites in a function body."""

    def __init__(self) -> None:
        self.call_linenos: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "enable_components"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ctx"
        ):
            self.call_linenos.append(node.lineno)
        self.generic_visit(node)


class _SkipGuardPresenceChecker(ast.NodeVisitor):
    """Check if a function body contains a _skip_notify (or similar) guard."""

    def __init__(self) -> None:
        self.has_skip_guard = False

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _SKIP_GUARD_NAMES:
            self.has_skip_guard = True
        self.generic_visit(node)


def _get_function_defs(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    fns: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fns.append(node)
    return fns


def test_enable_components_calls_have_notification_fallback():
    """Every ctx.enable_components call in server/tools/ must have a non-notification
    fallback guard or be in an exempt function (session-scoped unlock)."""
    from autoskillit.core import paths

    tools_dir = paths.pkg_root() / "server" / "tools"
    assert tools_dir.is_dir(), f"server/tools/ directory not found at {tools_dir}"

    violations: list[str] = []
    for py_file in sorted(tools_dir.glob("*.py")):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for fn in _get_function_defs(tree):
            if fn.name in _EXEMPT_FUNCTIONS:
                continue

            call_finder = _EnableComponentsCallFinder()
            call_finder.visit(fn)
            if not call_finder.call_linenos:
                continue

            guard_checker = _SkipGuardPresenceChecker()
            fn_module = ast.Module(body=fn.body, type_ignores=[])
            guard_checker.visit(fn_module)

            if not guard_checker.has_skip_guard:
                relpath = str(py_file.relative_to(paths.pkg_root()))
                for lineno in call_finder.call_linenos:
                    violations.append(
                        f"{relpath}:{lineno} — {fn.name}() calls ctx.enable_components "
                        f"without a non-notification fallback guard "
                        f"(add a _skip_notify check or add {fn.name!r} to _EXEMPT_FUNCTIONS)"
                    )

    assert not violations, (
        "ctx.enable_components calls without notification fallback:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
