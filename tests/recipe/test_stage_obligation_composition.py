"""Composition checks for publication-obligation gates and their bind inputs."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from autoskillit.core import load_yaml
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import extract_sentinel_json_blocks

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

PIPELINE_RECIPES = ("implementation", "remediation", "implementation-groups")
GATE_CALLABLE = "autoskillit.recipe._cmd_rpc.verify_stage_obligations"
BIND_SITES = {
    "implementation": ("bind_plan_set", "renew_plan_set"),
    "remediation": ("bind_plan_set", "renew_before_retry", "renew_plan_set"),
    "implementation-groups": ("bind_group_parts", "seal_plan_set", "renew_plan_set"),
}
GATE_SITES = {
    "implementation": (
        "verify_stage_obligations",
        "verify_stage_obligations_unconfirmed",
        "verify_stage_obligations_no_changes",
        "verify_stage_obligations_already_done",
    ),
    "remediation": (
        "verify_stage_obligations",
        "verify_stage_obligations_unconfirmed",
        "verify_stage_obligations_no_changes",
        "verify_stage_obligations_already_done",
    ),
    "implementation-groups": (
        "verify_stage_obligations",
        "verify_stage_obligations_unconfirmed",
    ),
}
SEALED_BIND = {
    "implementation": "bind_plan_set",
    "remediation": "bind_plan_set",
    "implementation-groups": "seal_plan_set",
}


def _load_projection(recipe_name: str, ingredient_overrides: dict[str, str]):
    result = load_and_validate(
        recipe_name,
        ingredient_overrides=ingredient_overrides,
        include_finalized_projection=True,
    )
    assert result["valid"] is True, result.get("errors", result.get("findings", []))
    return result, result["_finalized_projection"]


def _graph(projection) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for edge in projection.ordered_flow_edges:
        graph.setdefault(edge.source, []).append(edge.target)
    return graph


def _success_stops(content: str) -> set[str]:
    document = load_yaml(content)
    assert isinstance(document, dict)
    steps = document.get("steps")
    assert isinstance(steps, dict)
    success_stops: set[str] = set()
    for name, raw_step in steps.items():
        if not isinstance(raw_step, dict) or raw_step.get("action") != "stop":
            continue
        message = raw_step.get("message")
        if not isinstance(message, str):
            continue
        for block in extract_sentinel_json_blocks(message):
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("success") is True:
                success_stops.add(name)
                break
    return success_stops


def _success_reachable_without_gate(
    start: str, graph: dict[str, list[str]], gates: set[str], success_stops: set[str]
) -> str | None:
    queue = deque(graph.get(start, ()))
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        if node in success_stops:
            return node
        if node not in gates:
            queue.extend(graph.get(node, ()))
    return None


def _reaches_in_order(graph: dict[str, list[str]], names: tuple[str, ...]) -> bool:
    queue = deque([(names[0], 0)])
    visited: set[tuple[str, int]] = set()
    while queue:
        node, index = queue.popleft()
        if (node, index) in visited:
            continue
        visited.add((node, index))
        if node == names[index]:
            index += 1
        if index == len(names):
            return True
        queue.extend((target, index) for target in graph.get(node, ()))
    return False


def _assert_gates_dominate_success(
    result, projection, recipe_name: str, expected_gates: set[str] | None = None
) -> None:
    steps = {step.name: step for step in projection.ordered_steps}
    gates = {
        name
        for name, step in steps.items()
        if step.tool == "run_python" and step.with_args.get("callable") == GATE_CALLABLE
    }
    assert (expected_gates or set(GATE_SITES[recipe_name])) <= gates
    assert all(steps[name].skip_when_false is None for name in gates)
    sealed_binds = [
        step.name
        for step in steps.values()
        if step.tool == "bind_plan_set"
        and str(step.with_args.get("seal", "true")).strip("'\"").lower() == "true"
    ]
    assert SEALED_BIND[recipe_name] in sealed_binds
    graph = _graph(projection)
    success_stops = _success_stops(result["content"])
    for bind_name in sealed_binds:
        bypass = _success_reachable_without_gate(bind_name, graph, gates, success_stops)
        assert bypass is None, (
            f"{recipe_name}/{bind_name} reaches success stop {bypass!r} without a gate"
        )


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_open_pr_true_auto_merge_false_flow_crosses_the_gate_in_order(recipe_name: str) -> None:
    overrides = {"open_pr": "true", "auto_merge": "false"}
    if recipe_name == "implementation-groups":
        overrides["audit_impl"] = "true"
    result, projection = _load_projection(recipe_name, overrides)
    _assert_gates_dominate_success(result, projection, recipe_name)
    bind_name = SEALED_BIND[recipe_name]
    graph = _graph(projection)
    expected_flow = (
        bind_name,
        "audit_impl",
        "compose_pr",
        "route_queue_mode",
        "patch_token_summary",
        "verify_stage_obligations",
        "register_clone_success",
        "done",
    )
    assert _reaches_in_order(graph, expected_flow)


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_open_pr_false_keeps_gates_and_has_no_success_bypass(recipe_name: str) -> None:
    result, projection = _load_projection(recipe_name, {"open_pr": "false"})
    steps = {step.name: step for step in projection.ordered_steps}
    assert "compose_pr" not in steps
    reachable_gates = set(GATE_SITES[recipe_name]) - {"verify_stage_obligations_unconfirmed"}
    assert reachable_gates <= set(steps)
    _assert_gates_dominate_success(result, projection, recipe_name, reachable_gates)
    primary_bind = steps[SEALED_BIND[recipe_name]]
    assert primary_bind.with_args["open_pr"] == "${{ inputs.open_pr }}"


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_all_plan_set_binds_receive_stage_decision_inputs(recipe_name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    for name in BIND_SITES[recipe_name]:
        step = recipe.steps[name]
        assert step.with_args["open_pr"] == "${{ inputs.open_pr }}"
        assert step.with_args["auto_merge"] == "${{ inputs.auto_merge }}"
        assert step.on_result is not None
        default_route = next(
            condition.route for condition in step.on_result.conditions if condition.when is None
        )
        assert default_route == "release_issue_failure"


@pytest.mark.parametrize("recipe_name", ("implementation", "remediation"))
def test_parentless_reentry_bind_keeps_prior_authority(recipe_name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    bind = recipe.steps["bind_plan_set"]
    assert bind.with_args["prior_authority_path"] == "${{ context.plan_set_authority_path }}"
    assert "plan_set_authority_path" in bind.optional_context_refs


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_audit_impl_receives_the_bound_authority(recipe_name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert (
        recipe.steps["audit_impl"].with_args["skill_inputs"]["plan_set_authority_path"]
        == "${{ context.plan_set_authority_path }}"
    )


def test_skip_guard_on_gate_still_fails_after_pruning_clears_true_guard(tmp_path: Path) -> None:
    source = (builtin_recipes_dir() / "implementation.yaml").read_text(encoding="utf-8")
    original = "  verify_stage_obligations:\n    tool: run_python\n"
    mutated = source.replace(
        original,
        "  verify_stage_obligations:\n    skip_when_false: inputs.open_pr\n    tool: run_python\n",
        1,
    )
    assert mutated != source
    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "implementation.yaml").write_text(mutated, encoding="utf-8")

    result = load_and_validate(
        "implementation",
        project_dir=tmp_path,
        ingredient_overrides={"open_pr": "true"},
        include_finalized_projection=True,
    )
    assert result["valid"] is False, "a gate's skip_when_false error must survive pruning"
