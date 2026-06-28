"""Arch guard: any function in server/tools/ that checks DispatchStatus.RUNNING
must also verify liveness via is_dispatch_session_alive or resolve_stale_running.

Without this guard, new MCP tools can silently encode the bug pattern that
caused issue #4133 — treating a status claim as a fact without verifying
the owning process is alive.
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_LIVENESS_HELPERS: frozenset[str] = frozenset(
    {"is_dispatch_session_alive", "resolve_stale_running"}
)


class _RunningStatusChecker(ast.NodeVisitor):
    """Find DispatchStatus.RUNNING comparisons and verify liveness helper usage."""

    def __init__(self, fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.fn_node = fn_node
        self.running_check_linenos: list[int] = []
        self.has_liveness_helper = False

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._is_running_status_check(node):
            self.running_check_linenos.append(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_liveness_helper_call(node):
            self.has_liveness_helper = True
        self.generic_visit(node)

    def _is_running_status_check(self, node: ast.Compare) -> bool:
        if not isinstance(node.left, ast.Attribute):
            return False
        if node.left.attr != "status":
            return False
        for comparator in node.comparators:
            if not isinstance(comparator, ast.Attribute):
                continue
            if comparator.attr != "RUNNING":
                continue
            if not isinstance(node.ops[0], (ast.Eq, ast.Is)):
                continue
            return True
        return False

    def _is_liveness_helper_call(self, node: ast.Call) -> bool:
        if isinstance(node.func, ast.Name) and node.func.id in _LIVENESS_HELPERS:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LIVENESS_HELPERS:
            return True
        return False


def _get_function_defs(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    fns: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fns.append(node)
    return fns


def test_running_status_checks_require_liveness():
    """Every DispatchStatus.RUNNING check in server/tools/ must be guarded
    by a liveness verification (is_dispatch_session_alive or resolve_stale_running).
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
            checker = _RunningStatusChecker(fn)
            checker.visit(fn)

            if not checker.running_check_linenos:
                continue

            if checker.has_liveness_helper:
                continue

            relpath = str(py_file.relative_to(paths.pkg_root()))
            for lineno in checker.running_check_linenos:
                violations.append(
                    f"{relpath}:{lineno} — {fn.name}() checks "
                    "DispatchStatus.RUNNING without verifying liveness. "
                    f"Use {' or '.join(sorted(_LIVENESS_HELPERS))} "
                    "before treating RUNNING as a blocking fact."
                )

    assert not violations, "RUNNING status checks without liveness verification:\n" + "\n".join(
        f"  {v}" for v in violations
    )
