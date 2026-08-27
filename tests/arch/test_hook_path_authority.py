"""Architectural guard for hook-owned state-path resolution."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.small]

_STATE_PATH_HOOKS = (
    pkg_root() / "hooks" / "recipe_confirmed_post_hook.py",
    pkg_root() / "hooks" / "session_start_hook.py",
    pkg_root() / "hooks" / "_hook_settings.py",
)
_CENTRAL_ROOT_RESOLVERS = {
    "_hook_payload.py": {"resolve_state_root"},
    "_hook_settings.py": {"_default_state_root"},
}


def _function_cwd_calls(source_path: Path) -> Iterator[tuple[str, int]]:
    """Yield direct cwd calls with their enclosing function names."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_names: list[str] = []
            self.calls: list[tuple[str, int]] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_names.append(node.name)
            self.generic_visit(node)
            self.function_names.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and (node.func.value.id, node.func.attr) in {("Path", "cwd"), ("os", "getcwd")}
            ):
                scope = self.function_names[-1] if self.function_names else "<module>"
                self.calls.append((scope, node.lineno))
            self.generic_visit(node)

    visitor = _Visitor()
    visitor.visit(tree)
    yield from visitor.calls


def test_hook_state_consumers_do_not_independently_derive_cwd() -> None:
    """State consumers must delegate cwd fallback to the one root resolver."""
    violations = [
        f"{source_path.name}:{line} ({function_name})"
        for source_path in _STATE_PATH_HOOKS
        for function_name, line in _function_cwd_calls(source_path)
        if function_name not in _CENTRAL_ROOT_RESOLVERS.get(source_path.name, set())
    ]

    assert not violations, "independent hook state-root derivations: " + ", ".join(violations)


def test_only_central_resolvers_may_fall_back_to_process_cwd() -> None:
    """The two allowed cwd fallbacks remain explicit and reviewable."""
    resolver_paths = {
        pkg_root() / "hooks" / source_name: allowed_functions
        for source_name, allowed_functions in _CENTRAL_ROOT_RESOLVERS.items()
    }
    actual = {
        source_path.name: {function_name for function_name, _ in _function_cwd_calls(source_path)}
        for source_path in resolver_paths
    }

    assert actual == _CENTRAL_ROOT_RESOLVERS
