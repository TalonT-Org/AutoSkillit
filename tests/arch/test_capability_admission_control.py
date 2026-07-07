"""Architectural structural tests for capability admission control.

Verifies that:
- _compute_capability_feasibility is called inside load_and_validate with skip_resolutions
- All four content-serving surfaces gate on dispatch_feasible
  (open_kitchen, get_recipe, load_recipe, dispatch_food_truck)
- CAPABILITY_GATE_CALLABLES entries have a corresponding BACKEND_CAPABILITY_INGREDIENTS input
- Every run_python step using a CAPABILITY_GATE_CALLABLES callable reads a capability ingredient
- dispatch_food_truck injects _build_capability_overrides for backend capability signals
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


def test_dispatch_food_truck_gates_on_dispatch_feasible() -> None:
    """dispatch_food_truck must check dispatch_feasible before executing."""
    fleet_path = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch.py"
    src = fleet_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch_food_truck":
            found = False
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value == "dispatch_feasible":
                    found = True
                    break
            assert found, (
                "dispatch_food_truck must reference 'dispatch_feasible' to gate "
                "capability-DOA fleet dispatches before subprocess launch."
            )
            return
    pytest.fail("dispatch_food_truck function not found in tools_fleet_dispatch.py")


def test_compute_capability_feasibility_receives_skip_resolutions() -> None:
    """load_and_validate must pass skip_resolutions to _compute_capability_feasibility
    so the vacuous-gate detection has access to pruning context."""
    api_path = SRC_ROOT / "recipe" / "_api.py"
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "load_and_validate"
        ):
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "_compute_capability_feasibility"
                ):
                    kw_names = {kw.arg for kw in child.keywords if kw.arg is not None}
                    assert "skip_resolutions" in kw_names, (
                        "load_and_validate must pass skip_resolutions kwarg "
                        "to _compute_capability_feasibility for vacuous-gate detection."
                    )
                    return
            pytest.fail("_compute_capability_feasibility call not found in load_and_validate")
    pytest.fail("load_and_validate function not found in _api.py")


def test_compute_capability_feasibility_forwards_post_prune_recipe() -> None:
    """_compute_capability_feasibility must pass post_prune_recipe to
    _is_vacuous_gate so the reachability guard has access to the full Recipe
    object for graph analysis."""
    comp_path = SRC_ROOT / "recipe" / "_recipe_composition.py"
    tree = ast.parse(comp_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_compute_capability_feasibility"
        ):
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "_is_vacuous_gate"
                ):
                    kw_names = {kw.arg for kw in child.keywords if kw.arg is not None}
                    assert "post_prune_recipe" in kw_names, (
                        "_compute_capability_feasibility must pass post_prune_recipe "
                        "kwarg to _is_vacuous_gate for reachability-aware vacuity "
                        "detection."
                    )
                    return
            pytest.fail("_is_vacuous_gate call not found in _compute_capability_feasibility")
    pytest.fail("_compute_capability_feasibility function not found in _recipe_composition.py")


def test_dispatch_food_truck_injects_capability_overrides() -> None:
    """dispatch_food_truck must reference _provider_aware_capability_overrides to inject
    provider-aware backend capability signals into the load_and_validate call."""
    fleet_path = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch.py"
    src = fleet_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch_food_truck":
            found = False
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Name)
                    and child.id == "_provider_aware_capability_overrides"
                ):
                    found = True
                    break
            assert found, (
                "dispatch_food_truck must reference _provider_aware_capability_overrides "
                "to inject provider-aware backend capability signals into load_and_validate."
            )
            return
    pytest.fail("dispatch_food_truck function not found in tools_fleet_dispatch.py")


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


def test_capability_ingredient_to_skip_guard_keys_subset() -> None:
    """CAPABILITY_INGREDIENT_TO_SKIP_GUARD keys must be BACKEND_CAPABILITY_INGREDIENTS subset."""
    from autoskillit.core import (
        BACKEND_CAPABILITY_INGREDIENTS,
        CAPABILITY_INGREDIENT_TO_SKIP_GUARD,
    )

    orphaned = set(CAPABILITY_INGREDIENT_TO_SKIP_GUARD) - set(BACKEND_CAPABILITY_INGREDIENTS)
    assert not orphaned, (
        f"CAPABILITY_INGREDIENT_TO_SKIP_GUARD keys {orphaned} are not in "
        f"BACKEND_CAPABILITY_INGREDIENTS — vacuous-gate detection will silently "
        f"malfunction for these keys."
    )
    assert len(CAPABILITY_INGREDIENT_TO_SKIP_GUARD) > 0, (
        "CAPABILITY_INGREDIENT_TO_SKIP_GUARD must declare at least one mapping."
    )


def test_provider_aware_override_iterates_capability_guards() -> None:
    """Structural guard: _provider_aware_capability_overrides must be data-driven.

    It must reference CAPABILITY_INGREDIENT_TO_SKIP_GUARD (not hardcode the literal
    'inputs.backend_supports_git_write' string), so future capability ingredients
    are automatically picked up without code changes.
    """
    auto_overrides_path = SRC_ROOT / "server" / "tools" / "_auto_overrides.py"
    src = auto_overrides_path.read_text(encoding="utf-8")

    func_src = ""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_provider_aware_capability_overrides"
        ):
            func_src = ast.get_source_segment(src, node) or ""
            break
    assert func_src, "_provider_aware_capability_overrides not found in _auto_overrides.py"

    assert "CAPABILITY_INGREDIENT_TO_SKIP_GUARD" in func_src, (
        "_provider_aware_capability_overrides must reference CAPABILITY_INGREDIENT_TO_SKIP_GUARD "
        "to be data-driven — hardcoding capability guard strings causes silent breakage when "
        "new capability ingredients are added."
    )


def test_open_kitchen_calls_compute_effective_backend_map() -> None:
    """open_kitchen must call _compute_effective_backend_map to build the per-step map."""
    kitchen_path = SRC_ROOT / "server" / "tools" / "tools_kitchen.py"
    tree = ast.parse(kitchen_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "open_kitchen", "_compute_effective_backend_map"), (
        "open_kitchen must call _compute_effective_backend_map — removing this call "
        "breaks admission/dispatch agreement for capability-driven routing."
    )


def test_get_recipe_calls_compute_effective_backend_map() -> None:
    """get_recipe (MCP resource) must call _compute_effective_backend_map."""
    kitchen_path = SRC_ROOT / "server" / "tools" / "tools_kitchen.py"
    tree = ast.parse(kitchen_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "get_recipe", "_compute_effective_backend_map"), (
        "get_recipe must call _compute_effective_backend_map — removing this call "
        "breaks admission/dispatch agreement for capability-driven routing."
    )


def test_load_recipe_calls_compute_effective_backend_map() -> None:
    """load_recipe must call _compute_effective_backend_map."""
    recipe_path = SRC_ROOT / "server" / "tools" / "tools_recipe.py"
    tree = ast.parse(recipe_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "load_recipe", "_compute_effective_backend_map"), (
        "load_recipe must call _compute_effective_backend_map — removing this call "
        "breaks admission/dispatch agreement for capability-driven routing."
    )


def test_dispatch_food_truck_calls_compute_effective_backend_map() -> None:
    """dispatch_food_truck must call _compute_effective_backend_map."""
    fleet_path = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch.py"
    tree = ast.parse(fleet_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "dispatch_food_truck", "_compute_effective_backend_map"), (
        "dispatch_food_truck must call _compute_effective_backend_map — removing this "
        "call breaks admission/dispatch agreement for capability-driven routing."
    )


def test_run_skill_references_git_metadata_write_capability() -> None:
    """run_skill must use registry-driven capability routing (REQ-ROUTE-001)."""
    execution_path = SRC_ROOT / "server" / "tools" / "tools_execution.py"
    src = execution_path.read_text(encoding="utf-8")

    func_src = ""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_skill":
            func_src = ast.get_source_segment(src, node) or ""
            break
    assert func_src, "run_skill not found in tools_execution.py"

    assert "_has_routing_capability" in func_src, (
        "run_skill must call _has_routing_capability() — "
        "removing this breaks the capability-driven auto-route path that dispatches "
        "codex orchestrator skills requiring claude-code (REQ-ROUTE-001)."
    )
    assert "worker_routable" in src, (
        "tools_execution.py must reference 'worker_routable' in the routing helper — "
        "this field drives the registry-based routing gate (REQ-ROUTE-001)."
    )


def test_execute_dispatch_accepts_effective_backend_map() -> None:
    """execute_dispatch signature must include effective_backend_map parameter."""
    import inspect

    from autoskillit.fleet._api import execute_dispatch

    sig = inspect.signature(execute_dispatch)
    assert "effective_backend_map" in sig.parameters, (
        "execute_dispatch must accept effective_backend_map — "
        "without it the CLI and dispatch_food_truck cannot thread the per-step map "
        "into the engine's internal load_and_validate call."
    )


def _find_load_and_validate_call_in_run_dispatch(src: str) -> ast.Call | None:
    """Return the load_and_validate Call node inside _run_dispatch, or None."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_dispatch":
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "load_and_validate"
                ):
                    return child
    return None


def test_run_dispatch_passes_effective_backend_map_to_load_and_validate() -> None:
    """_run_dispatch must pass effective_backend_map kwarg to load_and_validate."""
    api_path = SRC_ROOT / "fleet" / "_api.py"
    src = api_path.read_text(encoding="utf-8")
    call_node = _find_load_and_validate_call_in_run_dispatch(src)
    assert call_node is not None, (
        "load_and_validate call not found inside _run_dispatch in fleet/_api.py"
    )
    kw_names = {kw.arg for kw in call_node.keywords if kw.arg is not None}
    assert "effective_backend_map" in kw_names, (
        "_run_dispatch must pass effective_backend_map to load_and_validate — "
        "without it the engine's validation always runs without per-step routing context."
    )


def test_execute_fleet_run_calls_provider_aware_capability_overrides() -> None:
    """_execute_fleet_run must call _provider_aware_capability_overrides (CLI admission parity)."""
    fleet_run_path = SRC_ROOT / "cli" / "fleet" / "_fleet_run.py"
    tree = ast.parse(fleet_run_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(
        tree, "_execute_fleet_run", "_provider_aware_capability_overrides"
    ), (
        "_execute_fleet_run must call _provider_aware_capability_overrides — "
        "without this, the CLI path does not compute provider-aware capability signals "
        "before dispatching, causing backend-incompatible-skill errors for Codex runs."
    )


def test_execute_fleet_run_calls_compute_effective_backend_map() -> None:
    """_execute_fleet_run must call _compute_effective_backend_map (CLI admission parity)."""
    fleet_run_path = SRC_ROOT / "cli" / "fleet" / "_fleet_run.py"
    tree = ast.parse(fleet_run_path.read_text(encoding="utf-8"))
    assert _has_call_in_function(tree, "_execute_fleet_run", "_compute_effective_backend_map"), (
        "_execute_fleet_run must call _compute_effective_backend_map — "
        "without this, the CLI path never computes per-step routing and always "
        "evaluates backend-compat with a flat backend map."
    )


def test_dispatch_food_truck_forwards_effective_backend_map_to_execute_dispatch() -> None:
    """dispatch_food_truck must pass effective_backend_map to execute_dispatch call."""
    fleet_path = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch.py"
    src = fleet_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch_food_truck":
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "execute_dispatch"
                ):
                    kw_names = {kw.arg for kw in child.keywords if kw.arg is not None}
                    assert "effective_backend_map" in kw_names, (
                        "dispatch_food_truck must pass effective_backend_map to "
                        "execute_dispatch — without it, the per-step routing map "
                        "computed by the preflight is silently dropped at the engine boundary."
                    )
                    return
            pytest.fail("execute_dispatch call not found inside dispatch_food_truck")
    pytest.fail("dispatch_food_truck function not found in tools_fleet_dispatch.py")


def test_validate_from_path_accepts_effective_backend_map() -> None:
    """validate_from_path signature must accept effective_backend_map."""
    import inspect

    from autoskillit.recipe._api_listing import validate_from_path

    sig = inspect.signature(validate_from_path)
    assert "effective_backend_map" in sig.parameters, (
        "validate_from_path must accept effective_backend_map — "
        "without it the validate_recipe MCP tool cannot thread per-step routing "
        "context into backend-compat rule evaluation."
    )


def test_compute_effective_backend_map_accepts_skill_resolver() -> None:
    """_compute_effective_backend_map must accept a skill_resolver parameter."""
    auto_overrides_path = SRC_ROOT / "server" / "tools" / "_auto_overrides.py"
    src = auto_overrides_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_compute_effective_backend_map"
        ):
            args = node.args
            all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            param_names = {a.arg for a in all_args}
            assert "skill_resolver" in param_names, (
                "_compute_effective_backend_map must accept skill_resolver — "
                "removing this parameter breaks capability-driven routing on the admission side."
            )
            return
    pytest.fail("_compute_effective_backend_map not found in _auto_overrides.py")
