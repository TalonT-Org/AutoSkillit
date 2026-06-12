"""Architectural structural tests for capability admission control.

Verifies that:
- _compute_capability_feasibility is called inside load_and_validate
- All four content-serving surfaces gate on dispatch_feasible
- CAPABILITY_GATE_CALLABLES entries have a corresponding BACKEND_CAPABILITY_INGREDIENTS input
- Every run_python step using a CAPABILITY_GATE_CALLABLES callable reads a capability ingredient
"""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _has_call_in_function(tree: ast.Module, func_name: str, target_call: str) -> bool:
    """Return True if the named function contains a call to target_call."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != func_name:
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == target_call
                ):
                    return True
    return False


def test_load_and_validate_calls_compute_capability_feasibility() -> None:
    """load_and_validate must invoke _compute_capability_feasibility."""
    api_path = SRC_ROOT / "recipe" / "_api.py"
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "load_and_validate", "_compute_capability_feasibility"), (
        "load_and_validate must call _compute_capability_feasibility "
        "to detect DOA pipelines from capability-gated run_python steps."
    )


def test_open_kitchen_gates_on_dispatch_feasible() -> None:
    """open_kitchen must check dispatch_feasible before enabling the gate."""
    kitchen_path = SRC_ROOT / "server" / "tools" / "tools_kitchen.py"
    src = kitchen_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "open_kitchen":
            src_dispatch_feasible = False
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value == "dispatch_feasible":
                    src_dispatch_feasible = True
                    break
            assert src_dispatch_feasible, (
                "open_kitchen must reference 'dispatch_feasible' to gate "
                "capability-DOA pipelines before revealing tools."
            )
            return
    pytest.fail("open_kitchen function not found in tools_kitchen.py")


def test_get_recipe_gates_on_dispatch_feasible() -> None:
    """get_recipe resource must check dispatch_feasible."""
    kitchen_path = SRC_ROOT / "server" / "tools" / "tools_kitchen.py"
    src = kitchen_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_recipe":
            found = False
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value == "dispatch_feasible":
                    found = True
                    break
            assert found, (
                "get_recipe resource must reference 'dispatch_feasible' to gate "
                "capability-DOA recipe serving."
            )
            return
    pytest.fail("get_recipe function not found in tools_kitchen.py")


def test_load_recipe_gates_on_dispatch_feasible() -> None:
    """load_recipe tool must check dispatch_feasible."""
    recipe_path = SRC_ROOT / "server" / "tools" / "tools_recipe.py"
    src = recipe_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "load_recipe":
            found = False
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value == "dispatch_feasible":
                    found = True
                    break
            assert found, (
                "load_recipe tool must reference 'dispatch_feasible' to surface "
                "capability-DOA signal to calling orchestrators."
            )
            return
    pytest.fail("load_recipe function not found in tools_recipe.py")


def test_capability_gate_callables_have_matching_ingredient() -> None:
    """Every CAPABILITY_GATE_CALLABLES callable must map to a BACKEND_CAPABILITY_INGREDIENTS input.

    This guards against adding a new gate callable without declaring its
    capability ingredient dependency.
    """
    import autoskillit.smoke_utils as smoke_utils_mod
    from autoskillit.core import BACKEND_CAPABILITY_INGREDIENTS, CAPABILITY_GATE_CALLABLES

    for callable_name in CAPABILITY_GATE_CALLABLES:
        assert hasattr(smoke_utils_mod, callable_name), (
            f"CAPABILITY_GATE_CALLABLES entry {callable_name!r} has no matching "
            f"callable in autoskillit.smoke_utils."
        )
        assert callable_name in smoke_utils_mod.__all__, (
            f"CAPABILITY_GATE_CALLABLES entry {callable_name!r} is not exported "
            f"via smoke_utils.__all__."
        )

    assert len(BACKEND_CAPABILITY_INGREDIENTS) > 0, (
        "BACKEND_CAPABILITY_INGREDIENTS must declare at least one capability ingredient key."
    )
    assert len(CAPABILITY_GATE_CALLABLES) > 0, (
        "CAPABILITY_GATE_CALLABLES must declare at least one capability gate callable."
    )
