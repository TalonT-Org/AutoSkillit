"""AST structural guard: kitchen_id assignment is only via resolve_kitchen_id()."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_kitchen_id_only_assigned_via_resolve_kitchen_id():
    """No code outside resolve_kitchen_id() may assign ctx.kitchen_id directly.

    This prevents future boot paths from diverging by replicating the assignment logic.
    All boot-path handlers must call resolve_kitchen_id() for kitchen_id assignment.
    """
    src = (
        "src/autoskillit/server/_lifespan.py",
        "src/autoskillit/server/tools/tools_kitchen.py",
    )

    canonical_assign_linenos: set[str] = set()

    for file_path in src:
        full_path = Path(__file__).parent.parent.parent / file_path
        tree = ast.parse(full_path.read_text(), filename=file_path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Attribute) and target.attr == "kitchen_id"):
                    continue
                if isinstance(node.value, ast.Call):
                    if getattr(node.value.func, "id", None) == "resolve_kitchen_id":
                        canonical_assign_linenos.add(f"{file_path}:{node.lineno}")
                        continue
                    rhs = ast.unparse(node.value)
                    pytest.fail(
                        f"{file_path}:{node.lineno}: "
                        f"ctx.kitchen_id assignment uses "
                        f"{rhs}, not resolve_kitchen_id()"
                    )
                else:
                    pytest.fail(
                        f"{file_path}:{node.lineno}: "
                        f"ctx.kitchen_id assignment is not via resolve_kitchen_id()"
                    )

    assert canonical_assign_linenos, (
        "No ctx.kitchen_id = resolve_kitchen_id() assignments found in scanned files — "
        "guard is vacuous"
    )
