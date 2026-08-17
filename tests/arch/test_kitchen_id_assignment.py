"""AST structural guard: kitchen_id assignment is only via resolve_kitchen_id()."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_kitchen_id_only_assigned_via_transition_bootstrap():
    """Assignments must mint at bootstrap or consume/reset the stored transition ID.

    This prevents request handling from reminting a kitchen identity after the
    infrastructure transition already owns one.
    """
    pkg_dirs = (
        Path(__file__).parent.parent.parent / "src" / "autoskillit" / "server" / "_lifespan",
        Path(__file__).parent.parent.parent
        / "src"
        / "autoskillit"
        / "server"
        / "tools"
        / "tools_kitchen",
    )

    canonical_assign_linenos: set[str] = set()
    stored_assign_linenos: set[str] = set()

    for pkg_dir in pkg_dirs:
        for py_path in sorted(pkg_dir.rglob("*.py")):
            file_path_rel = str(py_path.relative_to(Path(__file__).parent.parent.parent))
            tree = ast.parse(py_path.read_text(), filename=file_path_rel)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Attribute) and target.attr == "kitchen_id"):
                    continue
                if isinstance(node.value, ast.Call):
                    if getattr(node.value.func, "id", None) == "resolve_kitchen_id":
                        canonical_assign_linenos.add(f"{file_path_rel}:{node.lineno}")
                        continue
                    rhs = ast.unparse(node.value)
                    pytest.fail(
                        f"{file_path_rel}:{node.lineno}: "
                        f"ctx.kitchen_id assignment uses "
                        f"{rhs}, not resolve_kitchen_id()"
                    )
                if (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "kitchen_id"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "state"
                ):
                    stored_assign_linenos.add(f"{file_path_rel}:{node.lineno}")
                    continue
                if isinstance(node.value, ast.Constant) and node.value.value == "":
                    continue
                pytest.fail(
                    f"{file_path_rel}:{node.lineno}: ctx.kitchen_id assignment must mint via "
                    "resolve_kitchen_id(), consume transition_state.kitchen_id, or reset empty"
                )

    assert canonical_assign_linenos, (
        "No ctx.kitchen_id = resolve_kitchen_id() assignments found in scanned files — "
        "guard is vacuous"
    )
    assert stored_assign_linenos, (
        "No ctx.kitchen_id = transition_state.kitchen_id assignments found — "
        "request handling is not consuming bootstrap identity"
    )
