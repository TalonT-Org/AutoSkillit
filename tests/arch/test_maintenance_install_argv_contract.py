"""Architectural guard: every ``--maintenance-update`` argv must come from
``MaintenanceInstallArgv.to_argv()``.

Issue #4485's root cause was three production sites hand-building argv
literals for ``autoskillit install --maintenance-update`` and bypassing
the typed contract that would have enforced ``--expected-version``. This
AST guard makes the structural invariant permanent: any ast.List literal
containing the ``--maintenance-update`` string outside the canonical
builder module is a violation.

Pattern: ast.walk() over every .py file under src/autoskillit, skipping
the allowlist. Each ast.List literal is inspected for any constant elt
equal to ``--maintenance-update``. Found literals are reported as
violations with file:line:violation-line-number.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


# Files that legitimately contain the literal (the canonical builder).
_ALLOWLIST: frozenset[Path] = frozenset(
    {
        Path("src/autoskillit/cli/_install_contract.py"),
    },
)

_SRC_ROOT = Path("src/autoskillit")


def _scan_for_maintenance_update_literals(tree: ast.AST) -> list[int]:
    """Return line numbers of ast.List literals containing ``--maintenance-update``."""
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and elt.value == "--maintenance-update":
                    found.append(node.lineno)
                    break
    return found


def test_no_hand_built_maintenance_update_argv() -> None:
    """No production code may hand-build argv containing ``--maintenance-update``.

    Use ``MaintenanceInstallArgv.to_argv()`` instead. The single allowlist
    is the canonical builder itself; any other site is a regression of
    issue #4485.
    """
    src_root = Path(_SRC_ROOT)
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if py_file in _ALLOWLIST:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for lineno in _scan_for_maintenance_update_literals(tree):
            violations.append(f"{py_file}:{lineno}: hand-built argv with --maintenance-update")
    assert not violations, (
        "Use MaintenanceInstallArgv.to_argv() instead of hand-building argv:\n"
        + "\n".join(violations)
    )
