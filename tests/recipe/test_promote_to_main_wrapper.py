import functools

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPE_PATH = pkg_root() / "recipes" / "promote-to-main-wrapper.yaml"


@functools.lru_cache(maxsize=1)
def _load():
    return load_recipe(_RECIPE_PATH)


def test_recipe_parses() -> None:
    recipe = _load()
    assert recipe.name == "promote-to-main-wrapper"


def test_recipe_validates_cleanly() -> None:
    from autoskillit.core.types import Severity
    from tests.recipe.conftest import NO_AUTOSKILLIT_IMPORT

    recipe = _load()
    findings = run_semantic_rules(recipe)
    errors = [
        f for f in findings if f.severity == Severity.ERROR and f.rule != NO_AUTOSKILLIT_IMPORT
    ]
    undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
    assert errors == [], errors
    assert undeclared == [], undeclared


def test_source_dir_is_required_no_default() -> None:
    recipe = _load()
    ing = recipe.ingredients["source_dir"]
    assert ing.required is True
    assert ing.default is None


def test_promote_step_has_fault_guards() -> None:
    step = _load().steps["promote"]
    assert step.on_failure == "escalate_stop"
    assert step.on_context_limit == "escalate_stop"


def test_verdict_routing() -> None:
    step = _load().steps["promote"]
    conditions = step.on_result.conditions
    preflight = next((c for c in conditions if c.when and "preflight_failed" in c.when), None)
    assert preflight is not None, "no preflight_failed condition found"
    assert preflight.route == "escalate_stop"
    fallback = next((c for c in conditions if c.when is None), None)
    assert fallback is not None, "no fallback condition (when=None) found"
    assert fallback.route == "emit_result"


def test_category_summary_captured() -> None:
    step = _load().steps["promote"]
    assert "category_summary" in step.capture


def test_emit_result_echoes_all_tokens() -> None:
    cmd = _load().steps["emit_result"].with_args["cmd"]
    for token in ("pr_url", "verdict", "category_summary"):
        assert f"context.{token}" in cmd, f"missing context substitution for: {token}"


def test_requires_packs_declared() -> None:
    assert _load().requires_packs == ["github"]


def test_kind_is_food_truck() -> None:
    recipe = _load()
    assert recipe.kind == "food-truck"
