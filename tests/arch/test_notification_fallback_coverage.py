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

# The guard variable names that signal a non-notification fallback.
# `_use_global_enable` is the post-#4399 name (replaces `_skip_notify`); the old
# name is kept so any in-flight rename passes both old and new symbols until the
# rename lands everywhere.
_SKIP_GUARD_NAMES: frozenset[str] = frozenset({"_skip_notify", "_use_global_enable"})


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
    """Check if a function body contains a guard name in `_SKIP_GUARD_NAMES`."""

    def __init__(self) -> None:
        self.has_skip_guard = False

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _SKIP_GUARD_NAMES:
            self.has_skip_guard = True
        self.generic_visit(node)


class _GuardBranchNotificationFinder(ast.NodeVisitor):
    """Verify a guarded `if`-branch's body contains a `send_notification` call.

    Scoped to the `ast.If.body` (not the whole function) so an unrelated
    `send_notification` elsewhere in the function does not vacuously satisfy
    the check. Only branches whose test references a guard name in
    `_SKIP_GUARD_NAMES` are inspected.
    """

    def __init__(self) -> None:
        self.guard_branch_without_notification: list[int] = []

    def _test_references_guard(self, node: ast.expr) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in _SKIP_GUARD_NAMES:
                return True
        return False

    def _body_calls_send_notification(self, body: list[ast.stmt]) -> bool:
        for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "send_notification"
            ):
                return True
        return False

    def visit_If(self, node: ast.If) -> None:
        if self._test_references_guard(node.test) and not self._body_calls_send_notification(
            node.body
        ):
            self.guard_branch_without_notification.append(node.lineno)
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
                        f"(add a guard name in _SKIP_GUARD_NAMES or add {fn.name!r} to "
                        f"_EXEMPT_FUNCTIONS)"
                    )

    assert not violations, (
        "ctx.enable_components calls without notification fallback:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_guard_branches_send_notification():
    """Every guarded `if`-branch (test references a name in `_SKIP_GUARD_NAMES`)
    must contain a `send_notification` call in its body. Prevents silent
    notification suppression while a guard variable still exists.

    Scoped to the `if`-branch body so an unrelated `send_notification` elsewhere
    in the function does not vacuously satisfy the check.
    """
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
            finder = _GuardBranchNotificationFinder()
            finder.visit(fn)
            relpath = str(py_file.relative_to(paths.pkg_root()))
            for lineno in finder.guard_branch_without_notification:
                violations.append(
                    f"{relpath}:{lineno} — {fn.name}() guard branch lacks a "
                    f"send_notification() call in its `if`-branch body "
                    f"(guard names: {sorted(_SKIP_GUARD_NAMES)})"
                )

    assert not violations, "Guard branches without send_notification:\n" + "\n".join(
        f"  {v}" for v in violations
    )
