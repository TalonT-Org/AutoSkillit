"""Tests for campaign semantic validation rules (rules_campaign.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import autoskillit.recipe  # noqa: F401 -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.core.types import CaptureEntrySpec
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _cap(from_: str) -> CaptureEntrySpec:
    """Shorthand to build a CaptureEntrySpec in tests."""
    return CaptureEntrySpec(from_=from_)


def _standard_recipe(**kwargs: object) -> Recipe:
    return Recipe(
        name="standard",
        description="standard recipe",
        steps={"stop": RecipeStep(action="stop", message="done")},
        kitchen_rules=["NEVER"],
        **kwargs,
    )


def _campaign(**kwargs: object) -> Recipe:
    defaults: dict = {
        "name": "my-campaign",
        "description": "test campaign",
        "kind": RecipeKind.CAMPAIGN,
        "dispatches": [
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="Do the thing",
                ingredients={"task": "Do it"},
            )
        ],
        "requires_recipe_packs": ["implementation-family"],
        "kitchen_rules": ["NEVER"],
    }
    defaults.update(kwargs)
    return Recipe(**defaults)


def _findings(recipe: Recipe, rule: str, **ctx_kwargs: object) -> list:
    ctx = make_validation_context(recipe, **ctx_kwargs)
    return [f for f in run_semantic_rules(ctx) if f.rule == rule]


def _write_recipe_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# T15: campaign-kind-is-campaign
# ---------------------------------------------------------------------------


def test_campaign_kind_is_campaign_fires_on_wrong_kind():
    recipe = _standard_recipe(
        dispatches=[CampaignDispatch(name="p1", recipe="impl", task="do it")],
    )
    found = _findings(recipe, "campaign-kind-is-campaign")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T16: campaign-has-dispatches
# ---------------------------------------------------------------------------


def test_campaign_has_dispatches_fires_on_empty():
    recipe = _campaign(dispatches=[])
    found = _findings(recipe, "campaign-has-dispatches")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T17: dispatch-names-unique
# ---------------------------------------------------------------------------


def test_dispatch_names_unique_detects_duplicates():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="impl", task="a"),
            CampaignDispatch(name="phase-one", recipe="impl", task="b"),
        ]
    )
    found = _findings(recipe, "dispatch-names-unique")
    assert found
    assert found[0].severity == Severity.ERROR
    assert "phase-one" in found[0].message


# ---------------------------------------------------------------------------
# T18: dispatch-names-kebab-case
# ---------------------------------------------------------------------------


def test_dispatch_names_kebab_case_warns_on_underscore():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase_one", recipe="impl", task="do it"),
        ]
    )
    found = _findings(recipe, "dispatch-names-kebab-case")
    assert found
    assert found[0].severity == Severity.WARNING
    assert "phase_one" in found[0].message


# ---------------------------------------------------------------------------
# T19: dispatch-recipe-exists
# ---------------------------------------------------------------------------


def test_dispatch_recipe_exists_fires_on_unknown():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="unknown-recipe", task="do it"),
        ]
    )
    found = _findings(
        recipe,
        "dispatch-recipe-exists",
        available_recipes=frozenset({"implementation", "research"}),
    )
    assert found
    assert "unknown-recipe" in found[0].message


# ---------------------------------------------------------------------------
# T20: dispatch-recipe-is-standard
# ---------------------------------------------------------------------------


def test_campaign_rejects_dispatch_of_campaign_recipe(tmp_path: Path):
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-campaign.yaml",
        {
            "name": "target-campaign",
            "description": "another campaign",
            "kind": "campaign",
            "kitchen_rules": ["NEVER"],
            "dispatches": [
                {
                    "name": "sub-phase",
                    "recipe": "implementation",
                    "task": "work",
                }
            ],
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="target-campaign", task="do it"),
        ]
    )
    found = _findings(recipe, "dispatch-recipe-is-standard", project_dir=tmp_path)
    assert found
    assert found[0].severity == Severity.ERROR


def test_dispatch_recipe_is_standard_allows_food_truck_target(tmp_path: Path):
    """Campaign dispatching to a food-truck-kind recipe produces no findings."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-food-truck.yaml",
        {
            "name": "target-food-truck",
            "description": "a food truck",
            "kind": "food-truck",
            "kitchen_rules": ["NEVER"],
            "steps": {"done": {"action": "stop", "message": "sentinel done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="target-food-truck", task="do it"),
        ]
    )
    found = _findings(recipe, "dispatch-recipe-is-standard", project_dir=tmp_path)
    assert found == []


# ---------------------------------------------------------------------------
# T21: dispatch-recipe-in-declared-packs
# ---------------------------------------------------------------------------


def test_dispatch_recipe_in_declared_packs_warns(tmp_path: Path):
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "research-recipe.yaml",
        {
            "name": "research-recipe",
            "description": "research",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "categories": ["research-family"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        requires_recipe_packs=["implementation-family"],
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="research-recipe", task="do it"),
        ],
    )
    found = _findings(recipe, "dispatch-recipe-in-declared-packs", project_dir=tmp_path)
    assert found
    assert found[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# T22: campaign-requires-recipe-packs-exist
# ---------------------------------------------------------------------------


def test_campaign_requires_recipe_packs_exist_warns_on_unknown():
    recipe = _campaign(requires_recipe_packs=["nonexistent-family"])
    found = _findings(recipe, "campaign-requires-recipe-packs-exist")
    assert found
    assert found[0].severity == Severity.WARNING
    assert "nonexistent-family" in found[0].message


def test_campaign_requires_recipe_packs_exist_no_warning_for_known_pack():
    recipe = _campaign(requires_recipe_packs=["implementation-family"])
    found = _findings(recipe, "campaign-requires-recipe-packs-exist")
    assert not found


# ---------------------------------------------------------------------------
# T23: dispatch-ingredients-keys-in-target-schema
# ---------------------------------------------------------------------------


def test_dispatch_ingredients_keys_in_target_schema_fires(tmp_path: Path):
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it", "nonexistent_key": "value"},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-ingredients-keys-in-target-schema", project_dir=tmp_path)
    assert found
    assert "nonexistent_key" in found[0].message


# ---------------------------------------------------------------------------
# T24: dispatch-ingredient-values-are-strings
# ---------------------------------------------------------------------------


def test_dispatch_ingredient_values_are_strings_fires_on_non_string():
    dispatch = CampaignDispatch(
        name="phase-one",
        recipe="impl",
        task="do it",
        ingredients={"key": 123},  # type: ignore[arg-type]
    )
    recipe = _campaign(dispatches=[dispatch])
    found = _findings(recipe, "dispatch-ingredient-values-are-strings")
    assert found
    assert found[0].severity == Severity.ERROR
    assert "key" in found[0].message


# ---------------------------------------------------------------------------
# T25: depends-on-refers-to-valid-dispatches
# ---------------------------------------------------------------------------


def test_depends_on_refers_to_valid_dispatches_fires():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="impl",
                task="do it",
                depends_on=["nonexistent"],
            ),
        ]
    )
    found = _findings(recipe, "depends-on-refers-to-valid-dispatches")
    assert found
    assert "nonexistent" in found[0].message


# ---------------------------------------------------------------------------
# T26: depends-on-acyclic (cycle)
# ---------------------------------------------------------------------------


def test_depends_on_acyclic_detects_cycle():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="a", recipe="impl", task="a", depends_on=["c"]),
            CampaignDispatch(name="b", recipe="impl", task="b", depends_on=["a"]),
            CampaignDispatch(name="c", recipe="impl", task="c", depends_on=["b"]),
        ]
    )
    found = _findings(recipe, "depends-on-acyclic")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T27: depends-on-acyclic (DAG — no cycle)
# ---------------------------------------------------------------------------


def test_depends_on_acyclic_passes_on_dag():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="a", recipe="impl", task="a", depends_on=[]),
            CampaignDispatch(name="b", recipe="impl", task="b", depends_on=["a"]),
            CampaignDispatch(name="c", recipe="impl", task="c", depends_on=["b"]),
        ]
    )
    found = _findings(recipe, "depends-on-acyclic")
    assert not found


# ---------------------------------------------------------------------------
# T-S1: campaign-dispatch-depends-on-is-sequential
# ---------------------------------------------------------------------------


def test_campaign_dispatch_depends_on_is_sequential_fires_on_fan_in():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="impl", task="a", depends_on=[]),
            CampaignDispatch(name="phase-two", recipe="impl", task="b", depends_on=[]),
            CampaignDispatch(
                name="phase-merge",
                recipe="impl",
                task="c",
                depends_on=["phase-one", "phase-two"],
            ),
        ]
    )
    found = _findings(recipe, "campaign-dispatch-depends-on-is-sequential")
    assert len(found) == 1
    assert found[0].severity == Severity.ERROR
    assert "phase-merge" in found[0].message
    assert "phase-one" in found[0].message or "phase-two" in found[0].message


def test_campaign_dispatch_depends_on_is_sequential_passes_on_linear_chain():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="a", recipe="impl", task="a", depends_on=[]),
            CampaignDispatch(name="b", recipe="impl", task="b", depends_on=["a"]),
            CampaignDispatch(name="c", recipe="impl", task="c", depends_on=["b"]),
            CampaignDispatch(name="d", recipe="impl", task="d", depends_on=["c"]),
        ]
    )
    found = _findings(recipe, "campaign-dispatch-depends-on-is-sequential")
    assert found == []


# ---------------------------------------------------------------------------
# T28: campaign-task-non-empty
# ---------------------------------------------------------------------------


def test_campaign_task_non_empty_fires():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="impl", task=""),
        ]
    )
    found = _findings(recipe, "campaign-task-non-empty")
    assert found
    assert "phase-one" in found[0].message


# ---------------------------------------------------------------------------
# T29: autoskillit-version-compatible
# ---------------------------------------------------------------------------


def test_autoskillit_version_compatible_warns():
    recipe = _campaign(version="999.0.0")
    found = _findings(recipe, "autoskillit-version-compatible")
    assert found
    assert found[0].severity == Severity.WARNING
    assert "999.0.0" in found[0].message


# ---------------------------------------------------------------------------
# T30: standard recipe skips all campaign rules
# ---------------------------------------------------------------------------


def test_campaign_rules_skip_for_standard_recipe():
    recipe = _standard_recipe()
    all_findings = run_semantic_rules(recipe)
    campaign_findings = [
        f
        for f in all_findings
        if f.rule.startswith("campaign-")
        or f.rule.startswith("dispatch-")
        or f.rule.startswith("depends-on-")
    ]
    assert not campaign_findings, (
        f"Campaign rules must not fire on standard recipe: {campaign_findings}"
    )


# ---------------------------------------------------------------------------
# T31: valid campaign passes all rules
# ---------------------------------------------------------------------------


def test_campaign_valid_passes_all_rules():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="Implement the feature",
                ingredients={"task": "Implement the feature"},
            ),
            CampaignDispatch(
                name="phase-two",
                recipe="implementation",
                task="Write tests",
                depends_on=["phase-one"],
            ),
        ],
        requires_recipe_packs=["implementation-family"],
        version="0.1.0",
    )
    ctx = make_validation_context(
        recipe,
        available_recipes=frozenset({"implementation"}),
    )
    all_findings = run_semantic_rules(ctx)
    _is_campaign_rule = lambda f: (  # noqa: E731
        f.rule.startswith("campaign-")
        or f.rule.startswith("dispatch-")
        or f.rule.startswith("depends-on-")
    )
    error_findings = [
        f for f in all_findings if f.severity == Severity.ERROR and _is_campaign_rule(f)
    ]
    warning_findings = [
        f for f in all_findings if f.severity == Severity.WARNING and _is_campaign_rule(f)
    ]
    assert not error_findings, f"Valid campaign must not have ERROR findings: {error_findings}"
    assert not warning_findings, (
        f"Valid campaign must not have WARNING findings: {warning_findings}"
    )


# ---------------------------------------------------------------------------
# T32: dispatch-capture-keys-are-identifiers
# ---------------------------------------------------------------------------


def test_capture_key_must_be_identifier():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="t",
                capture={"bad-key": _cap("${{ result.v }}")},
            )
        ]
    )
    findings = _findings(recipe, "dispatch-capture-keys-are-identifiers")
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T33: dispatch-capture-value-references-result
# ---------------------------------------------------------------------------


def test_capture_value_must_reference_result():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="t",
                capture={"k": _cap("not_a_template")},
            )
        ]
    )
    findings = _findings(recipe, "dispatch-capture-value-references-result")
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T34: campaign-ingredient-refs-have-prior-capture (unresolvable ref)
# ---------------------------------------------------------------------------


def test_campaign_ingredient_ref_requires_prior_capture():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="t",
            ),
            CampaignDispatch(
                name="phase-two",
                recipe="implementation",
                task="t",
                ingredients={"x": "${{ campaign.x }}"},
                depends_on=["phase-one"],
            ),
        ]
    )
    findings = _findings(recipe, "campaign-ingredient-refs-have-prior-capture")
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T35: campaign-ingredient-refs-have-prior-capture (satisfied by ancestor)
# ---------------------------------------------------------------------------


def test_campaign_ingredient_ref_satisfied_by_ancestor():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="t",
                capture={"x": _cap("${{ result.x }}")},
            ),
            CampaignDispatch(
                name="phase-two",
                recipe="implementation",
                task="t",
                ingredients={"x": "${{ campaign.x }}"},
                depends_on=["phase-one"],
            ),
        ]
    )
    findings = _findings(recipe, "campaign-ingredient-refs-have-prior-capture")
    assert findings == []


# ---------------------------------------------------------------------------
# T36: valid capture spec passes
# ---------------------------------------------------------------------------


def test_valid_capture_spec_passes():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="implementation",
                task="t",
                capture={"out_file": _cap("${{ result.out_file }}")},
            )
        ]
    )
    capture_key_findings = _findings(recipe, "dispatch-capture-keys-are-identifiers")
    capture_val_findings = _findings(recipe, "dispatch-capture-value-references-result")
    assert capture_key_findings == []
    assert capture_val_findings == []


# ---------------------------------------------------------------------------
# T-G1: gate-dispatch-valid-type
# ---------------------------------------------------------------------------


def test_gate_dispatch_valid_type_fires_on_unknown_value():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="approve", message="Approve?")]
    )
    found = _findings(recipe, "gate-dispatch-valid-type")
    assert found
    assert found[0].severity == Severity.ERROR


def test_gate_dispatch_valid_type_passes_for_confirm():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?")]
    )
    found = _findings(recipe, "gate-dispatch-valid-type")
    assert found == []


# ---------------------------------------------------------------------------
# T-G3: gate-dispatch-has-message
# ---------------------------------------------------------------------------


def test_gate_dispatch_has_message_fires_on_empty_message():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="confirm", message="")]
    )
    found = _findings(recipe, "gate-dispatch-has-message")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T-G4: gate-dispatch-no-recipe
# ---------------------------------------------------------------------------


def test_gate_dispatch_no_recipe_fires_when_recipe_is_set():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="gate-check",
                gate="confirm",
                recipe="some-recipe",
                task="do it",
                message="Proceed?",
            )
        ]
    )
    found = _findings(recipe, "gate-dispatch-no-recipe")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T-G5: gate-dispatch-no-capture
# ---------------------------------------------------------------------------


def test_gate_dispatch_no_capture_fires_when_capture_is_set():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="gate-check",
                gate="confirm",
                message="Proceed?",
                capture={"key": _cap("${{ result.val }}")},
            )
        ]
    )
    found = _findings(recipe, "gate-dispatch-no-capture")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T-G6: campaign-task-non-empty exempts gate dispatches
# ---------------------------------------------------------------------------


def test_campaign_task_non_empty_exempts_gate_dispatches():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?")]
    )
    found = _findings(recipe, "campaign-task-non-empty")
    assert found == []


# ---------------------------------------------------------------------------
# T-G7: dispatch-recipe-exists exempts gate dispatches
# ---------------------------------------------------------------------------


def test_dispatch_recipe_exists_exempts_gate_dispatches():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?")]
    )
    found = _findings(
        recipe, "dispatch-recipe-exists", available_recipes=frozenset({"some-other-recipe"})
    )
    assert found == []


# ---------------------------------------------------------------------------
# T-G8: dispatch-recipe-is-standard exempts gate dispatches
# ---------------------------------------------------------------------------


def test_dispatch_recipe_is_standard_exempts_gate_dispatches():
    recipe = _campaign(
        dispatches=[CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?")]
    )
    found = _findings(recipe, "dispatch-recipe-is-standard", project_dir=None)
    assert found == []


# ---------------------------------------------------------------------------
# T-G9: dispatch-ingredients-keys-in-target-schema exempts gate dispatches
# ---------------------------------------------------------------------------


def test_dispatch_ingredients_keys_exempts_gate_dispatches():
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="gate-check",
                gate="confirm",
                message="Proceed?",
                ingredients={"foo": "bar"},
            )
        ]
    )
    found = _findings(recipe, "dispatch-ingredients-keys-in-target-schema")
    assert found == []


# ---------------------------------------------------------------------------
# T-D1: campaign-dangling-ingredient
# ---------------------------------------------------------------------------


def test_campaign_dangling_ingredient_fires_on_missing_forwarding(tmp_path: Path):
    """Rule fires when campaign ingredient is declared by target but dispatch omits it."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "review_pr": {"description": "Review PR", "default": "false"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        ingredients={"review_pr": {"description": "Review PR", "default": "false"}},
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert len(found) == 1
    assert found[0].severity == Severity.WARNING
    assert "review_pr" in found[0].message


def test_campaign_dangling_ingredient_passes_when_forwarded(tmp_path: Path):
    """Rule passes when ingredient is forwarded."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "review_pr": {"description": "Review PR", "default": "false"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        ingredients={"review_pr": {"description": "Review PR", "default": "false"}},
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"review_pr": "${{ inputs.review_pr }}"},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


def test_campaign_dangling_ingredient_passes_when_target_does_not_declare(tmp_path: Path):
    """Rule passes when target sub-recipe does not declare the ingredient."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "other_ing": {"description": "Other", "default": "x"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        ingredients={"output_mode": {"description": "Output mode", "default": "local"}},
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


def test_campaign_dangling_ingredient_skips_unloadable_target(tmp_path: Path):
    """Rule skips when target recipe is not loadable."""
    recipe = _campaign(
        ingredients={"review_pr": {"description": "Review PR", "default": "false"}},
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="nonexistent-recipe",
                task="do it",
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


def test_campaign_dangling_ingredient_fires_per_dispatch(tmp_path: Path):
    """Rule fires per-dispatch for multi-dispatch campaigns."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-forwarding.yaml",
        {
            "name": "target-forwarding",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "output_mode": {"description": "Output mode", "default": "local"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    _write_recipe_yaml(
        recipes_dir / "target-omitting.yaml",
        {
            "name": "target-omitting",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "output_mode": {"description": "Output mode", "default": "local"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        ingredients={"output_mode": {"description": "Output mode", "default": "local"}},
        dispatches=[
            CampaignDispatch(
                name="phase-forward",
                recipe="target-forwarding",
                task="do it",
                ingredients={"output_mode": "${{ inputs.output_mode }}"},
            ),
            CampaignDispatch(
                name="phase-omit",
                recipe="target-omitting",
                task="do it",
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert len(found) == 1
    assert "phase-omit" in found[0].message
    assert "phase-forward" not in found[0].message


def test_campaign_dangling_ingredient_ignores_gate_dispatches(tmp_path: Path):
    """Rule ignores gate dispatches."""
    recipe = _campaign(
        ingredients={"review_pr": {"description": "Review PR", "default": "false"}},
        dispatches=[
            CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?"),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


def test_campaign_dangling_ingredient_ignores_non_campaign_recipes(tmp_path: Path):
    """Rule ignores non-campaign recipes."""
    recipe = _standard_recipe()
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


def test_campaign_dangling_ingredient_exempts_task(tmp_path: Path):
    """Rule accounts for task auto-injection and should not fire for it."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        ingredients={"task": {"description": "The task", "required": True}},
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="Do the thing",  # task field set, but not forwarded in ingredients
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "campaign-dangling-ingredient", project_dir=tmp_path)
    assert found == []


# ---------------------------------------------------------------------------
# T-R1: dispatch-required-ingredient-provided
# ---------------------------------------------------------------------------


def test_dispatch_required_ingredient_provided_fires_on_missing(tmp_path: Path):
    """Rule fires when target recipe has a required ingredient the dispatch doesn't provide."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
                "source_dir": {"description": "Source directory", "required": True},
                "base_branch": {"description": "Base branch", "default": "main"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it"},
                # source_dir is required but not provided
            ),
        ]
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert len(found) == 1
    assert found[0].severity == Severity.ERROR
    assert "source_dir" in found[0].message


def test_dispatch_required_ingredient_provided_passes_when_all_provided(tmp_path: Path):
    """Rule passes when all required ingredients are provided."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
                "source_dir": {"description": "Source directory", "required": True},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it", "source_dir": "/tmp/src"},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


def test_dispatch_required_ingredient_provided_ignores_defaulted(tmp_path: Path):
    """Rule does not fire for required ingredients that have a default."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
                "base_branch": {"description": "Base branch", "required": True, "default": "main"},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it"},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


def test_dispatch_required_ingredient_provided_exempts_task_auto_injection(tmp_path: Path):
    """Rule does not fire when target declares task as required with no default."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={},  # task is auto-injected from dispatch.task field
            ),
        ]
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


def test_dispatch_required_ingredient_provided_skips_gate_dispatches(tmp_path: Path):
    """Rule skips gate dispatches which have no target recipe."""
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?"),
        ],
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


def test_dispatch_required_ingredient_provided_skips_unloadable_target(tmp_path: Path):
    """Rule skips when target recipe cannot be loaded."""
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="nonexistent-recipe",
                task="do it",
                ingredients={},
            ),
        ],
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


def test_dispatch_required_ingredient_provided_fires_per_dispatch(tmp_path: Path):
    """Rule fires per-dispatch; multi-dispatch campaign with one missing."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {
                "task": {"description": "The task", "required": True},
                "source_dir": {"description": "Source directory", "required": True},
            },
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it", "source_dir": "/tmp/src"},
            ),
            CampaignDispatch(
                name="phase-two",
                recipe="target-recipe",
                task="do it",
                ingredients={"task": "do it"},
                # source_dir missing
            ),
        ],
    )
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert len(found) == 1
    assert "phase-two" in found[0].message
    assert "phase-one" not in found[0].message


def test_dispatch_required_ingredient_provided_skips_non_campaign(tmp_path: Path):
    """Rule skips non-campaign recipes."""
    recipe = _standard_recipe()
    found = _findings(recipe, "dispatch-required-ingredient-provided", project_dir=tmp_path)
    assert found == []


# ---------------------------------------------------------------------------
# T-S2: dispatch-capture-field-in-sentinel
# ---------------------------------------------------------------------------


def test_dispatch_capture_field_in_sentinel_fires_on_unknown_field(tmp_path: Path):
    """Rule fires when capture references a field not in target's sentinel."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {
                "done": {
                    "action": "stop",
                    "message": (
                        "Emit the L3 result sentinel JSON block now. "
                        'Example sentinel: {"success": true, "output_path": "<path>"}'
                    ),
                }
            },
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                capture={"bad_field": _cap("${{ result.nonexistent_field }}")},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert len(found) == 1
    assert found[0].severity == Severity.ERROR
    assert "nonexistent_field" in found[0].message


def test_dispatch_capture_field_in_sentinel_passes_when_field_exists(tmp_path: Path):
    """Rule passes when capture field matches sentinel fields."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {
                "done": {
                    "action": "stop",
                    "message": (
                        "Emit the L3 result sentinel JSON block now. "
                        'Example sentinel: {"success": true, "output_path": "<path>"}'
                    ),
                }
            },
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                capture={"output_path": _cap("${{ result.output_path }}")},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_checks_any_sentinel(tmp_path: Path):
    """Rule passes if field is in ANY sentinel stop step."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {
                "path-a": {
                    "action": "stop",
                    "message": ('Example sentinel: {"success": true, "alpha": "<val>"}'),
                },
                "path-b": {
                    "action": "stop",
                    "message": ('Example sentinel: {"success": true, "beta": "<val>"}'),
                },
            },
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                capture={"alpha": _cap("${{ result.alpha }}")},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_skips_no_sentinel(tmp_path: Path):
    """Rule silently skips when target has no sentinel stop step."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {"stop": {"action": "stop", "message": "done"}},
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                capture={"output_path": _cap("${{ result.output_path }}")},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_skips_no_capture(tmp_path: Path):
    """Rule silently skips when dispatch has no capture block."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "target-recipe.yaml",
        {
            "name": "target-recipe",
            "description": "target",
            "kind": "standard",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {
                "done": {
                    "action": "stop",
                    "message": ('Example sentinel: {"success": true, "output_path": "<path>"}'),
                }
            },
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="target-recipe",
                task="do it",
                capture={},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_skips_gate_dispatches(tmp_path: Path):
    """Rule skips gate dispatches."""
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?"),
        ],
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_skips_unloadable_target(tmp_path: Path):
    """Rule skips when target recipe cannot be loaded."""
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="nonexistent-recipe",
                task="do it",
                capture={"output_path": _cap("${{ result.output_path }}")},
            ),
        ],
    )
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


def test_dispatch_capture_field_in_sentinel_skips_non_campaign(tmp_path: Path):
    """Rule skips non-campaign recipes."""
    recipe = _standard_recipe()
    found = _findings(recipe, "dispatch-capture-field-in-sentinel", project_dir=tmp_path)
    assert found == []


# ---------------------------------------------------------------------------
# T-S3: _extract_sentinel_fields
# ---------------------------------------------------------------------------


def test_extract_sentinel_fields_parses_json_example():
    """Helper parses JSON example block and returns field names."""
    from autoskillit.recipe.rules.rules_campaign import _extract_sentinel_fields
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "done": RecipeStep(
                action="stop",
                message=(
                    "Emit the L3 result sentinel JSON block now. "
                    'Example sentinel: {"success": true, "output_path": "<path>"}'
                ),
            )
        },
        kitchen_rules=["NEVER"],
    )
    fields = _extract_sentinel_fields(recipe)
    assert fields == {"success", "output_path"}


def test_extract_sentinel_fields_returns_empty_for_no_json():
    """Helper returns empty set when no JSON example block is present."""
    from autoskillit.recipe.rules.rules_campaign import _extract_sentinel_fields
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        steps={"done": RecipeStep(action="stop", message="done")},
        kitchen_rules=["NEVER"],
    )
    fields = _extract_sentinel_fields(recipe)
    assert fields == set()


def test_extract_sentinel_fields_handles_multiline_json():
    """Helper handles JSON spanning multiple lines (folded YAML block)."""
    from autoskillit.recipe.rules.rules_campaign import _extract_sentinel_fields
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "done": RecipeStep(
                action="stop",
                message=(
                    "Example sentinel:\n"
                    "  {\n"
                    '    "success": true,\n'
                    '    "output_path": "<path>",\n'
                    '    "errors": []\n'
                    "  }"
                ),
            )
        },
        kitchen_rules=["NEVER"],
    )
    fields = _extract_sentinel_fields(recipe)
    assert fields == {"success", "output_path", "errors"}


def test_extract_sentinel_fields_handles_multiple_sentinels():
    """Helper returns union of fields from all sentinel stop steps."""
    from autoskillit.recipe.rules.rules_campaign import _extract_sentinel_fields
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "path-a": RecipeStep(
                action="stop",
                message='Example sentinel: {"success": true, "alpha": "<val>"}',
            ),
            "path-b": RecipeStep(
                action="stop",
                message='Example sentinel: {"success": true, "beta": "<val>"}',
            ),
        },
        kitchen_rules=["NEVER"],
    )
    fields = _extract_sentinel_fields(recipe)
    assert fields == {"success", "alpha", "beta"}


def test_dispatch_capture_field_in_sentinel_severity_is_error():
    """Guard: dispatch-capture-field-in-sentinel must be ERROR, not WARNING.

    Phantom captures that pass validation at WARNING severity caused
    cascading runtime failures in campaign dispatch chains (see #2276).
    This test prevents the severity from being downgraded.
    """
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule = next(
        (r for r in _RULE_REGISTRY if r.name == "dispatch-capture-field-in-sentinel"), None
    )
    assert rule is not None, "Rule 'dispatch-capture-field-in-sentinel' not found in registry"
    assert rule.severity == Severity.ERROR


def test_dispatch_capture_field_in_all_sentinels_severity_is_error():
    """Guard: dispatch-capture-field-in-all-sentinels must be ERROR, not WARNING."""
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule = next(
        (r for r in _RULE_REGISTRY if r.name == "dispatch-capture-field-in-all-sentinels"), None
    )
    assert rule is not None, "Rule 'dispatch-capture-field-in-all-sentinels' not found in registry"
    assert rule.severity == Severity.ERROR


def test_dispatch_capture_field_in_all_sentinels_fires_on_path_exclusive_field(tmp_path):
    """A capture referencing a field emitted by only ONE sentinel path must fire ERROR.

    Reproduces the review_local_complete gap: pr_url is emitted by
    review_pr_complete but not review_local_complete. The union check
    passes, but the per-path check must fail.
    """
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_recipe_yaml(
        recipes_dir / "dual-sentinel-target.yaml",
        {
            "name": "dual-sentinel-target",
            "description": "target with two sentinel paths",
            "kind": "food-truck",
            "kitchen_rules": ["NEVER"],
            "ingredients": {"task": {"description": "t", "required": True}},
            "steps": {
                "path_a": {
                    "action": "stop",
                    "message": (
                        "Emit the L3 result sentinel JSON block now. "
                        'Example sentinel: {"success": true, "pr_url": "<url>", '
                        '"report_path": "<path>"}'
                    ),
                },
                "path_b": {
                    "action": "stop",
                    "message": (
                        "Emit the L3 result sentinel JSON block now. "
                        'Example sentinel: {"success": true, "local_path": "<path>"}'
                    ),
                },
            },
        },
    )
    recipe = _campaign(
        dispatches=[
            CampaignDispatch(
                name="phase-one",
                recipe="dual-sentinel-target",
                task="do it",
                capture={"pr_url": _cap("${{ result.pr_url }}")},
            ),
        ]
    )
    found = _findings(recipe, "dispatch-capture-field-in-all-sentinels", project_dir=tmp_path)
    assert len(found) == 1
    assert found[0].severity == Severity.ERROR
    assert "pr_url" in found[0].message
    assert "not all sentinel" in found[0].message.lower()
