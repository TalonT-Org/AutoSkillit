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
    recipe = _load()
    findings = run_semantic_rules(recipe)
    assert findings == [], findings


def test_source_dir_is_required_no_default() -> None:
    recipe = _load()
    ing = recipe.ingredients["source_dir"]
    assert ing.required is True
    assert ing.default in (None, "")


def test_promote_step_has_fault_guards() -> None:
    step = _load().steps["promote"]
    assert step.on_failure == "escalate_stop"
    assert step.on_context_limit == "escalate_stop"


def test_verdict_routing() -> None:
    step = _load().steps["promote"]
    conditions = step.on_result.conditions
    preflight = next(c for c in conditions if c.when and "preflight_failed" in c.when)
    assert preflight.route == "escalate_stop"
    fallback = next(c for c in conditions if c.when is None)
    assert fallback.route == "emit_result"


def test_category_summary_captured() -> None:
    step = _load().steps["promote"]
    assert "category_summary" in step.capture


def test_emit_result_echoes_all_tokens() -> None:
    cmd = _load().steps["emit_result"].with_args["cmd"]
    for token in ("pr_url", "verdict", "category_summary"):
        assert token in cmd, f"missing token: {token}"


def test_requires_packs_declared() -> None:
    assert _load().requires_packs
