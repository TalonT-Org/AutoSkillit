"""AST guard: no raw signal.signal(SIGTERM, ...) in cli/app.py.

SIGTERM must be handled via anyio.open_signal_receiver (event-loop-routed
callback), not via signal.signal() (frame-interrupting KeyboardInterrupt that
escapes the C-level event-loop runner before finally: blocks can fire).

This guard is paired with the ruff TID251 ban in pyproject.toml which catches
the pattern at lint time. The AST test provides exact SIGTERM-specific matching
as a regression gate.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server")]

_APP_PATH = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli" / "app.py"
_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _is_sigterm_call(node: ast.Call) -> bool:
    """Return True if this is signal.signal(<SIGTERM variant>, ...)."""
    func = node.func
    # Must be signal.signal(...)
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "signal"
        and isinstance(func.value, ast.Name)
        and func.value.id == "signal"
    ):
        return False
    if not node.args:
        return False
    first_arg = node.args[0]
    # signal.SIGTERM or bare SIGTERM
    if isinstance(first_arg, ast.Attribute) and first_arg.attr == "SIGTERM":
        return True
    if isinstance(first_arg, ast.Name) and first_arg.id == "SIGTERM":
        return True
    return False


class TestNoRawSignalHandler:
    def test_ast_no_signal_signal_sigterm_in_app_py(self):
        """AST walk: cli/app.py must not call signal.signal(SIGTERM, ...)."""
        source = _APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        bad_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_sigterm_call(node)
        ]
        assert not bad_calls, (
            f"Found {len(bad_calls)} raw signal.signal(SIGTERM, ...) call(s) in cli/app.py. "
            "Use anyio.open_signal_receiver instead — see _serve_with_signal_guard."
        )

    def test_grep_no_signal_signal_sigterm_in_src(self):
        """Regex grep: no signal.signal(...SIGTERM...) pattern across src/autoskillit."""
        pattern = re.compile(r"signal\.signal\s*\(.*SIGTERM")
        violations: list[str] = []
        for py_file in _SRC_ROOT.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(f"{py_file.relative_to(_SRC_ROOT)}:{lineno}: {line.strip()}")
        assert not violations, (
            "Found raw signal.signal(SIGTERM, ...) usage in src/autoskillit:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\nUse anyio.open_signal_receiver(signal.SIGTERM) — see _serve_with_signal_guard."
        )
