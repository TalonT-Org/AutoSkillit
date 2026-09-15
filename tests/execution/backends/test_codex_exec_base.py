"""Arch guard: no raw `["codex", "exec", ...]` list literal in CodexBackend.

Every CodexBackend command builder now speaks the app-server transport via
shared command-builder helpers; this guards against a raw exec-transport
argv literal creeping back into any of them.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _find_raw_codex_exec_list_literal(class_body: ast.ClassDef) -> ast.List | None:
    """Return the first constant ``["codex", "exec", ...]`` literal in AST order."""
    for node in ast.walk(class_body):
        if not isinstance(node, ast.List):
            continue
        elements = node.elts
        if len(elements) < 2:
            continue
        first_two = []
        for element in elements[:2]:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                first_two.append(element.value)
        if first_two == ["codex", "exec"]:
            return node
    return None


class TestNoRawCodexExecListLiteral:
    pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

    def test_no_raw_codex_exec_list_literal_in_codex_backend(self) -> None:
        source = inspect.getsource(CodexBackend)
        tree = ast.parse(source)

        class_body = tree.body[0]
        assert isinstance(class_body, ast.ClassDef)

        node = _find_raw_codex_exec_list_literal(class_body)
        if node is None:
            return

        func_name = "<unknown>"
        for parent in ast.walk(class_body):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(parent):
                    if child is node:
                        func_name = parent.name
                        break
        raise AssertionError(
            f"Raw ['codex', 'exec', ...] list literal found in "
            f"CodexBackend.{func_name}. Codex no longer speaks the exec "
            f"transport for this construction path; route through the "
            f"shared command-builder helpers instead of inlining argv."
        )
