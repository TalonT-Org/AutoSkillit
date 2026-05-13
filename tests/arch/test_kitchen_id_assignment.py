"""AST structural guard: kitchen_id assignment is only via resolve_kitchen_id()."""

from __future__ import annotations

import ast

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

    for file_path in src:
        full_path = pytest.importorskip("pathlib").Path(__file__).parent.parent.parent / file_path
        tree = ast.parse(full_path.read_text(), filename=file_path)

        resolve_kitchen_id_body: set[int] = set()
        canonical_assign_linenos: set[int] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_kitchen_id":
                for child in ast.walk(node):
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            resolve_kitchen_id_body.add(child.lineno)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    is_kitchen_id_attr = (
                        isinstance(target, ast.Attribute) and target.attr == "kitchen_id"
                    )
                    if is_kitchen_id_attr:
                        is_resolve_kitchen_id_scope = any(
                            n.lineno in resolve_kitchen_id_body
                            for n in ast.walk(node)
                            if isinstance(n, ast.Assign) and n.targets[0] == target
                        )
                        if not is_resolve_kitchen_id_scope:
                            if isinstance(node.value, ast.Call):
                                if getattr(node.value.func, "id", None) == "resolve_kitchen_id":
                                    canonical_assign_linenos.add(node.lineno)
                                else:
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
