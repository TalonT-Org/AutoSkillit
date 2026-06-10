"""AST guard: direct swap_labels calls in fleet/ must go through cleanup_orphaned_labels.

Issue #3983 root cause: a parallel implementation of label cleanup in _reset.py
diverged from the canonical implementation in _label_cleanup.py. This guard
prevents future regressions by failing CI if any code under src/autoskillit/fleet/
(except _label_cleanup.py itself) calls github_client.swap_labels directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "fleet"
EXEMPT_FILES: frozenset[str] = frozenset({"_label_cleanup.py"})


def _find_swap_labels_calls(path: Path) -> list[int]:
    """Return line numbers of `.swap_labels(` attribute calls in a file."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "swap_labels":
            lines.append(node.lineno)
    return lines


def test_no_direct_swap_labels_in_fleet_cleanup_paths() -> None:
    """All swap_labels calls in fleet/ must live in _label_cleanup.py.

    Any direct call in another module would re-introduce the parallel-
    implementation divergence that caused issue #3983.
    """
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.glob("*.py")):
        if py_file.name in EXEMPT_FILES:
            continue
        for lineno in _find_swap_labels_calls(py_file):
            violations.append(f"{py_file.name}:{lineno}")

    assert not violations, (
        "Direct swap_labels calls in fleet/ must go through "
        f"cleanup_orphaned_labels in _label_cleanup.py. Found in: {violations}"
    )
