"""Structural enforcement: all CLI subprocess.run calls that inherit the
terminal (no capture_output, no stdout=PIPE/DEVNULL) must be wrapped in
a terminal_guard() context manager.

Follows the same AST-walk pattern as test_input_tty_contracts.py.

DEPENDENCY: Requires Part A to be applied (cli/_terminal.py must exist and
both interactive subprocess.run calls must be wrapped) before this test passes.
"""
import ast
from pathlib import Path

import pytest

CLI_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"

# Files that contain no subprocess.run calls — skip for clarity
# (test will skip automatically if no subprocess.run found in source)

# subprocess.run calls with these keyword args are capturing (non-interactive)
# and are exempt from the terminal_guard() requirement.
_EXEMPT_KWARGS = frozenset({"capture_output", "stdout"})


def _is_capturing_call(call_node: ast.Call) -> bool:
    """Return True if this subprocess.run call captures or redirects stdout."""
    for kw in call_node.keywords:
        if kw.arg in _EXEMPT_KWARGS:
            return True
    return False


def _collect_violations(source: str, filename: str) -> list[int]:
    """Return line numbers of non-capturing subprocess.run calls outside terminal_guard().

    Parses the module AST and tracks entry/exit into `with terminal_guard():`
    context manager blocks. Any subprocess.run call found outside such a block
    that does not use capture_output or redirect stdout is a violation.
    """
    tree = ast.parse(source, filename=filename)
    violations: list[int] = []

    class GuardTracker(ast.NodeVisitor):
        def __init__(self) -> None:
            self._guard_depth = 0

        def visit_With(self, node: ast.With) -> None:
            entered = False
            for item in node.items:
                ctx = item.context_expr
                if (
                    isinstance(ctx, ast.Call)
                    and isinstance(ctx.func, ast.Name)
                    and ctx.func.id == "terminal_guard"
                ):
                    entered = True
            if entered:
                self._guard_depth += 1
            self.generic_visit(node)
            if entered:
                self._guard_depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            is_subprocess_run = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            )
            if (
                is_subprocess_run
                and not _is_capturing_call(node)
                and self._guard_depth == 0
            ):
                violations.append(node.lineno)
            self.generic_visit(node)

    GuardTracker().visit(tree)
    return violations


@pytest.mark.parametrize("py_file", sorted(CLI_DIR.glob("*.py")))
def test_interactive_subprocess_calls_wrapped_in_terminal_guard(py_file: Path) -> None:
    """Every non-capturing subprocess.run call in cli/ must be inside terminal_guard().

    This test is the structural immune system for the terminal raw-mode bug class
    (GitHub Issue #509). It prevents any future interactive subprocess.run call
    from being added to the CLI layer without terminal state management.

    If this test fails with your change:
        1. You added a subprocess.run call in a CLI module without capture_output=True
        2. Wrap it: `with terminal_guard(): result = subprocess.run(...)`
        3. Import: `from autoskillit.cli._terminal import terminal_guard`
    """
    source = py_file.read_text()
    if "subprocess.run" not in source:
        return  # skip files with no subprocess calls

    violations = _collect_violations(source, str(py_file))
    assert violations == [], (
        f"\n\n{py_file.name}: interactive subprocess.run found at line(s) "
        f"{violations} without terminal_guard() wrapper.\n\n"
        f"Fix: wrap with `with terminal_guard():` and import from "
        f"`autoskillit.cli._terminal`.\n\n"
        f"See: tests/cli/test_input_tty_contracts.py for the analogous pattern."
    )
