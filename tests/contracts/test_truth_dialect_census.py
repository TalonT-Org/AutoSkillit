"""Census the intentionally distinct truth dialects used by production."""

from __future__ import annotations

import pytest

from autoskillit.core import DeclaredTruthUnsupported, normalize_declared_truth
from autoskillit.execution.backends.claude import _active_agent_teams
from autoskillit.hooks import _hook_settings
from autoskillit.recipe._recipe_composition import _is_ingredient_truthy
from autoskillit.recipe._rule_helpers import _is_failure_sentinel_value
from autoskillit.recipe.schema import RecipeStep
from autoskillit.server.tools.tools_kitchen import _compute_unlocked_steps

pytestmark = pytest.mark.small

_DIALECT_RATIONALES = {
    "recipe-ingredient-pruning": (
        "Legacy skip_when_false composition keeps unknown non-empty values."
    ),
    "ingredient-lock-overlay": "The persisted kitchen overlay also treats off as locked.",
    "claude-agent-teams": "Only an affirmative setting may enable the experimental backend mode.",
    "quota-hook-disabled": (
        "The hook falls back to its configured default for an unknown environment value."
    ),
    "failure-sentinel": "Only explicit false signals a recipe stop failure.",
    "declared-step-guard": "Captured guard values are a closed execution contract.",
}


def test_truth_dialect_census_is_complete_and_explained() -> None:
    """A new truth parser must be added deliberately to this census."""
    assert set(_DIALECT_RATIONALES) == {
        "recipe-ingredient-pruning",
        "ingredient-lock-overlay",
        "claude-agent-teams",
        "quota-hook-disabled",
        "failure-sentinel",
        "declared-step-guard",
    }
    assert all(_DIALECT_RATIONALES.values())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        ("off", True),
        ("maybe", True),
    ],
)
def test_recipe_ingredient_pruning_dialect(value: str, expected: bool) -> None:
    assert _is_ingredient_truthy(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("maybe", True),
    ],
)
def test_ingredient_lock_overlay_dialect(value: str, expected: bool) -> None:
    unlocked = _compute_unlocked_steps(
        {"step": RecipeStep(skip_when_false="inputs.enabled")},
        {"enabled": value},
    )
    assert unlocked == {"step": expected}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("off", False),
        ("maybe", False),
    ],
)
def test_claude_agent_teams_dialect(value: str, expected: bool) -> None:
    assert _active_agent_teams(value) is expected


@pytest.mark.parametrize(
    ("value", "configured", "expected"),
    [
        ("1", False, True),
        ("true", False, True),
        ("yes", False, True),
        ("0", True, False),
        ("false", True, False),
        ("no", True, False),
        ("maybe", True, True),
    ],
)
def test_quota_hook_disabled_dialect(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    configured: bool,
    expected: bool,
) -> None:
    monkeypatch.setenv(_hook_settings.ENV_DISABLED, value)
    monkeypatch.setattr(_hook_settings, "_read_hook_config", lambda: {"disabled": configured})
    assert _hook_settings.resolve_quota_settings().disabled is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(False, True), ("false", True), ("FALSE", True), (0, False), ("off", False)],
)
def test_failure_sentinel_dialect(value: object, expected: bool) -> None:
    assert _is_failure_sentinel_value(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (" true ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        (False, False),
        (" false ", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_declared_step_guard_dialect(value: object, expected: bool) -> None:
    assert normalize_declared_truth(value) is expected


@pytest.mark.parametrize("value", ["maybe", "t", "y", "${{ context.guard }}"])
def test_declared_step_guard_rejects_values_outside_its_closed_dialect(value: str) -> None:
    with pytest.raises(DeclaredTruthUnsupported):
        normalize_declared_truth(value)
