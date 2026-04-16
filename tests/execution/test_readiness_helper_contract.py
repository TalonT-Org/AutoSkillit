"""AST lint guard: no inline stderr/stdout readline loops used as subprocess readiness polls.

Any test that spawns a subprocess and polls its stderr/stdout line-by-line to
wait for a readiness token is a structural race — the string-parse window is
outside the try: block of the lifespan's cancel scope. The approved pattern is
`wait_for_subprocess_ready` from `tests._subprocess_ready`.

This test walks every .py file under tests/ and rejects any file that:
  - contains a `.stderr.readline()` or `.stdout.readline()` call inside a
    while/for loop, AND
  - also contains a `send_signal` or `Popen` call in the same function, AND
  - does NOT import from tests._subprocess_ready (the approved helper).

Files that only readline for unrelated reasons (e.g., reading output after
the process is done) are not caught by this guard because they lack a loop
enclosing the call or lack a send_signal/Popen usage.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution")]

_TESTS_ROOT = Path(__file__).parent.parent


def _has_subprocess_signal(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function calls send_signal or Popen."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("send_signal", "Popen"):
            return True
        if isinstance(func, ast.Name) and func.id == "Popen":
            return True
    return False


def _has_inline_readline_loop(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function has a readline() call inside a while/for loop."""
    for loop in ast.walk(func_node):
        if not isinstance(loop, (ast.While, ast.For)):
            continue
        for node in ast.walk(loop):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "readline":
                # Check it's .stderr.readline or .stdout.readline
                value = func.value
                if isinstance(value, ast.Attribute) and value.attr in ("stderr", "stdout"):
                    return True
    return False


def _imports_subprocess_ready(tree: ast.Module) -> bool:
    """Return True if the file imports from tests._subprocess_ready."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "_subprocess_ready" in node.module:
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "_subprocess_ready" in alias.name:
                    return True
    return False


class TestReadinessHelperContract:
    def test_no_inline_readline_loops_in_tests(self):
        """No test file may use inline stderr/stdout readline loops as readiness polls."""
        violations: list[str] = []
        for py_file in _TESTS_ROOT.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except SyntaxError:
                continue

            # Files that import the approved helper are exempt
            if _imports_subprocess_ready(tree):
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _has_inline_readline_loop(node) and _has_subprocess_signal(node):
                    violations.append(
                        f"{py_file.relative_to(_TESTS_ROOT)}:{node.lineno}: "
                        f"function {node.name!r} has inline stderr/stdout readline loop"
                    )

        assert not violations, (
            "Found inline stderr/stdout readline readiness polls in test functions:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\nUse `from tests._subprocess_ready import wait_for_subprocess_ready` instead."
        )
