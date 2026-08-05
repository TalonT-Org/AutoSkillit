"""T-B6: cli/update/*.py must import every third-party module at module top.

The update transaction's failure path runs across an irreversible pivot
(``uv tool install --force``/``upgrade`` may rebuild the venv mid-process —
issue #4469). A function-local third-party import deferred until a
post-pivot ``except`` handler runs is the exact landmine shape B-I1 fixes
for structlog/rich in ``core/logging.py``: an import that can raise for
reasons unrelated to the condition being handled, at the worst possible
moment. The seams to inject failure and observe this exist (B-I2/B-I3); this
guard makes sure nothing reintroduces the underlying import-timing hazard.

First-party (``autoskillit.*``) lazy imports are unaffected — deferred
imports to avoid import cycles are a pervasive, deliberate pattern
throughout this codebase and are not the hazard this guard targets.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

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

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_Import(self, node: ast.Import) -> None:
            if self.depth > 0:
                for alias in node.names:
                    if _is_third_party(alias.name):
                        violations.append(f"{path.name}:{node.lineno}: import {alias.name}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self.depth > 0 and node.module is not None and node.level == 0:
                if _is_third_party(node.module):
                    violations.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
            self.generic_visit(node)

    _Visitor().visit(tree)
    return violations


def test_no_function_local_third_party_imports_in_update_package() -> None:
    """Every third-party import in cli/update/*.py must be at module top.

    Fails today on cli/update/_update_checks.py:92,204
    (``from packaging.version import Version``) before B-I4 hoists them.
    """
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
