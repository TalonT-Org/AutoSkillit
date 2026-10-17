"""Registry contracts for recipe checkpoint delivery outcomes."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    PRState,
    RecipeDeliveryOutcomeDef,
    ToolDef,
    ToolInitializationOperation,
    ToolParamDef,
    ToolParamRole,
)
from autoskillit.core._tool_registry_builders import _RECIPE_DELIVERY_OUTCOMES
from autoskillit.core.tool_registry import TOOL_REGISTRY

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _tool_def(
    *,
    automatic: bool = False,
    recovery: bool = False,
    outcome: RecipeDeliveryOutcomeDef | None = None,
    params: tuple[str, ...] = (),
) -> ToolDef:
    return ToolDef(
        name="test_tool",
        params=tuple(ToolParamDef(name, role=ToolParamRole.PROTOCOL) for name in params),
        initialization_operation=ToolInitializationOperation.INSPECTION,
        automatic_recipe_delivery=automatic,
        recovery_recipe_delivery=recovery,
        recipe_delivery_outcome=outcome,
    )


@pytest.mark.parametrize(
    ("automatic", "recovery", "outcome", "params"),
    [
        (True, False, None, ("step_name",)),
        (False, False, RecipeDeliveryOutcomeDef(), ("step_name",)),
        (True, False, RecipeDeliveryOutcomeDef(), ()),
    ],
    ids=("flag-needs-outcome", "outcome-needs-flag", "argument-step-needs-parameter"),
)
def test_tool_def_delivery_invariants(
    automatic: bool,
    recovery: bool,
    outcome: RecipeDeliveryOutcomeDef | None,
    params: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        _tool_def(
            automatic=automatic,
            recovery=recovery,
            outcome=outcome,
            params=params,
        )


@pytest.mark.parametrize(
    ("outcome", "payload", "expected"),
    [
        (
            RecipeDeliveryOutcomeDef(success_field="success", success_value=True),
            {"success": True},
            True,
        ),
        (
            RecipeDeliveryOutcomeDef(success_field="success", success_value=True),
            {"success": 1},
            False,
        ),
        (
            RecipeDeliveryOutcomeDef(success_field="reviews_posted", success_value="true"),
            {"reviews_posted": "true"},
            True,
        ),
        (
            RecipeDeliveryOutcomeDef(success_field="reviews_posted", success_value="true"),
            {"reviews_posted": True},
            False,
        ),
        (RecipeDeliveryOutcomeDef(), {}, True),
        (RecipeDeliveryOutcomeDef(), {"success": True}, True),
        (RecipeDeliveryOutcomeDef(), {"success": 1}, True),
        (RecipeDeliveryOutcomeDef(), {"error": "failed"}, False),
        (RecipeDeliveryOutcomeDef(), {"success": True, "error": "failed"}, False),
        (RecipeDeliveryOutcomeDef(), {"success": False}, False),
    ],
    ids=(
        "bool-identity-success",
        "bool-does-not-equal-integer",
        "string-equality-success",
        "string-does-not-equal-bool",
        "implicit-success-empty-payload",
        "implicit-success-true-payload",
        "implicit-success-non-false-value",
        "implicit-failure-error-key",
        "implicit-failure-error-key-with-true-success",
        "implicit-failure-false-success",
    ),
)
def test_outcome_reader_semantics(
    outcome: RecipeDeliveryOutcomeDef,
    payload: dict[str, object],
    expected: bool,
) -> None:
    assert outcome.succeeded(payload) is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"success": True}, True), ({"success": False}, False)],
)
def test_recipe_observability_does_not_change_outcome_reading(
    payload: dict[str, object],
    expected: bool,
) -> None:
    outcome = RecipeDeliveryOutcomeDef(success_field="success", recipe_observable=False)

    assert outcome.succeeded(payload) is expected


def test_every_outcome_key_names_a_registered_tool() -> None:
    assert set(_RECIPE_DELIVERY_OUTCOMES) <= set(TOOL_REGISTRY)


def test_wait_for_merge_queue_delivery_outcome_uses_merged_state() -> None:
    outcome = TOOL_REGISTRY["wait_for_merge_queue"].recipe_delivery_outcome
    assert outcome is not None

    assert outcome.succeeded({"pr_state": PRState.MERGED.value}) is True
    assert outcome.succeeded({"success": False, "error": "x"}) is False


@pytest.mark.parametrize(
    "state",
    [state for state in PRState if state is not PRState.MERGED],
    ids=lambda state: state.value,
)
def test_wait_for_merge_queue_delivery_outcome_rejects_other_pr_states(
    state: PRState,
) -> None:
    outcome = TOOL_REGISTRY["wait_for_merge_queue"].recipe_delivery_outcome
    assert outcome is not None

    assert outcome.succeeded({"pr_state": state.value}) is False


def test_recipe_observable_defaults_true_for_registered_outcomes() -> None:
    assert RecipeDeliveryOutcomeDef().recipe_observable is True
    for tool_name in ("run_python", "complete_run_skill_result"):
        outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
        assert outcome is not None
        assert outcome.recipe_observable is False
    for tool_name, tool_def in TOOL_REGISTRY.items():
        outcome = tool_def.recipe_delivery_outcome
        if outcome is None:
            continue
        if tool_name not in {"run_python", "complete_run_skill_result"}:
            assert outcome.recipe_observable is True, tool_name
