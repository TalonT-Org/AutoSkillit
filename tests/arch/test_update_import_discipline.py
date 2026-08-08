"""Require update modules to import third-party dependencies before pivots."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.arch._helpers import _function_local_imports

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_STDLIB = frozenset(sys.stdlib_module_names) | frozenset(sys.builtin_module_names)
_UPDATE_PKG = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "cli" / "update"


def _top_level_module(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _is_third_party(module_name: str) -> bool:
    top = _top_level_module(module_name)
    return top not in _STDLIB and top not in ("autoskillit", "tests")


def _function_local_third_party_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in _function_local_imports(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_third_party(alias.name):
                    violations.append(f"{path.name}:{node.lineno}: import {alias.name}")
        elif node.module is not None and node.level == 0 and _is_third_party(node.module):
            violations.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
    return violations


def test_no_function_local_third_party_imports_in_update_package() -> None:
    """Every third-party import in cli/update/*.py must be at module top."""
    violations: list[str] = []
    for path in sorted(_UPDATE_PKG.glob("*.py")):
        violations.extend(_function_local_third_party_imports(path))
    assert not violations, (
        "Function-local third-party imports found in cli/update/ — hoist to "
        "module top so the failure path's imports are complete before the "
        "pivot:\n" + "\n".join(violations)
    )


def test_guard_detects_a_function_local_third_party_import() -> None:
    """Meta-test: the guard actually has teeth (would catch a real violation)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        offender = Path(tmp) / "offender.py"
        offender.write_text(
            "def f():\n    from packaging.version import Version\n    return Version\n"
        )
        violations = _function_local_third_party_imports(offender)
    assert len(violations) == 1
    assert "packaging.version" in violations[0]


def test_guard_ignores_function_local_autoskillit_imports() -> None:
    """First-party lazy imports (this codebase's circular-import-avoidance
    pattern) must not be flagged.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.py"
        clean.write_text(
            "def f():\n    from autoskillit.cli._init_helpers import _is_plugin_installed\n"
            "    import json\n    return _is_plugin_installed, json\n"
        )
        violations = _function_local_third_party_imports(clean)
    assert violations == []
