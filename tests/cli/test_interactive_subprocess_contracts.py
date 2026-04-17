"""Structural enforcement: all CLI subprocess.run calls that inherit the
terminal (no capture_output, no stdout=PIPE/DEVNULL) must be wrapped in
a terminal_guard() context manager.

Follows the same AST-walk pattern as test_input_tty_contracts.py.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

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
            if is_subprocess_run and not _is_capturing_call(node) and self._guard_depth == 0:
                violations.append(node.lineno)
            self.generic_visit(node)

    GuardTracker().visit(tree)
    return violations


def test_terminal_guard_never_emits_smcup_on_entry() -> None:
    """_terminal.py must not contain the smcup escape sequence (?1049h).

    terminal_guard() is an exit-only safety net. It emits ?1049l (rmcup)
    in its finally block as a safety net for abnormal subprocess exits, but
    must NEVER emit ?1049h (smcup) on entry. DECSET 1049 is a boolean toggle
    with no nesting counter — emitting it before a TUI subprocess launch
    (e.g. Claude Code Ink) overwrites the DECSC cursor save point and corrupts
    the TUI's viewport layout.

    This source-scan guard complements the mock-based behavioral test
    (test_does_not_emit_entry_alt_screen_sequence) and catches any future PR
    that attempts to re-add entry-side alt-screen sequences.

    Regression guard for: investigation_terminal_guard_alt_screen_scrollbar
    See: test_interactive_subprocess_calls_wrapped_in_terminal_guard for the
    analogous structural guard on subprocess call sites.
    """
    terminal_py = CLI_DIR / "_terminal.py"
    source = terminal_py.read_text()
    assert "?1049h" not in source, (
        f"{terminal_py.name} must not emit \\033[?1049h (smcup). "
        "terminal_guard() is an exit-only cleanup safety net. "
        "The subprocess (e.g. Claude Code Ink TUI) is the sole owner of "
        "alt-screen entry. See: test_does_not_emit_entry_alt_screen_sequence "
        "for the behavioral guard."
    )


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
        pytest.skip(f"{py_file.name}: no subprocess.run calls")

    violations = _collect_violations(source, str(py_file))
    assert violations == [], (
        f"\n\n{py_file.name}: interactive subprocess.run found at line(s) "
        f"{violations} without terminal_guard() wrapper.\n\n"
        f"Fix: wrap with `with terminal_guard():` and import from "
        f"`autoskillit.cli._terminal`.\n\n"
        f"See: tests/cli/test_input_tty_contracts.py for the analogous pattern."
    )
