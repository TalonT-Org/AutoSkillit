"""Tests for campaign semantic validation rules (rules_campaign.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import autoskillit.recipe  # noqa: F401 -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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
                capture={"bad-key": "${{ result.v }}"},
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
                capture={"k": "not_a_template"},
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
                capture={"x": "${{ result.x }}"},
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
                capture={"out_file": "${{ result.out_file }}"},
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
                capture={"key": "${{ result.val }}"},
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
