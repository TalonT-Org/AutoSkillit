"""Registry contracts for recipe checkpoint delivery outcomes."""

from __future__ import annotations

import pytest

from autoskillit.core import (
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


def test_every_outcome_key_names_a_registered_tool() -> None:
    assert set(_RECIPE_DELIVERY_OUTCOMES) <= set(TOOL_REGISTRY)
