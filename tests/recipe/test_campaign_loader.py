"""Tests for campaign recipe loader — _parse_recipe, list_campaign_recipes,
find_campaign_by_name, load_campaign_recipes_in_packs, and validate_recipe campaign branch."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from autoskillit.core import DispatchGateType, pkg_root
from autoskillit.recipe.io import (
    _parse_recipe,
    find_campaign_by_name,
    list_campaign_recipes,
    load_campaign_recipes_in_packs,
    load_recipe,
)
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


def _campaign_data(**overrides: object) -> dict:
    base: dict = {
        "name": "my-campaign",
        "description": "A test campaign",
        "kind": "campaign",
        "kitchen_rules": ["NEVER do bad things"],
        "dispatches": [
            {
                "name": "phase-one",
                "recipe": "implementation",
                "task": "Do the thing",
                "ingredients": {"task": "Do the thing"},
                "depends_on": [],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_recipe campaign field parsing
# ---------------------------------------------------------------------------


def test_parse_recipe_reads_campaign_kind():
    data = _campaign_data()
    recipe = _parse_recipe(data)
    assert recipe.kind == RecipeKind.CAMPAIGN


def test_parse_recipe_reads_dispatches():
    data = _campaign_data(
        dispatches=[
            {
                "name": "phase-a",
                "recipe": "implementation",
                "task": "First task",
                "ingredients": {"task": "First task"},
                "depends_on": [],
            },
            {
                "name": "phase-b",
                "recipe": "research",
                "task": "Second task",
                "ingredients": {},
                "depends_on": ["phase-a"],
            },
        ]
    )
    recipe = _parse_recipe(data)
    assert len(recipe.dispatches) == 2
    assert recipe.dispatches[0].name == "phase-a"
    assert recipe.dispatches[0].recipe == "implementation"
    assert recipe.dispatches[0].task == "First task"
    assert recipe.dispatches[0].ingredients == {"task": "First task"}
    assert recipe.dispatches[0].depends_on == []
    assert recipe.dispatches[1].name == "phase-b"
    assert recipe.dispatches[1].depends_on == ["phase-a"]


def test_parse_recipe_reads_campaign_metadata_fields():
    data = _campaign_data(
        categories=["implementation-family"],
        requires_recipe_packs=["implementation-family"],
        allowed_recipes=["special-recipe"],
        continue_on_failure=True,
    )
    recipe = _parse_recipe(data)
    assert recipe.categories == ["implementation-family"]
    assert recipe.requires_recipe_packs == ["implementation-family"]
    assert recipe.allowed_recipes == ["special-recipe"]
    assert recipe.continue_on_failure is True


def test_parse_recipe_defaults_campaign_fields_when_absent():
    data = {
        "name": "standard-recipe",
        "description": "No campaign fields",
        "kitchen_rules": ["NEVER"],
        "steps": {"stop": {"action": "stop", "message": "done"}},
    }
    recipe = _parse_recipe(data)
    assert recipe.kind == RecipeKind.STANDARD
    assert recipe.dispatches == []
    assert recipe.categories == []
    assert recipe.requires_recipe_packs == []
    assert recipe.allowed_recipes == []
    assert recipe.continue_on_failure is False


# ---------------------------------------------------------------------------
# list_campaign_recipes
# ---------------------------------------------------------------------------


def test_list_campaign_recipes_scans_campaigns_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import autoskillit.recipe.io as _rio

    monkeypatch.setattr(_rio, "pkg_root", lambda: tmp_path)
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "my-campaign.yaml",
        _campaign_data(name="my-campaign"),
    )
    result = list_campaign_recipes(tmp_path)
    assert len(result.items) == 1
    assert result.items[0].name == "my-campaign"


def test_list_campaign_recipes_returns_empty_when_no_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import autoskillit.recipe.io as _rio

    monkeypatch.setattr(_rio, "pkg_root", lambda: tmp_path)
    result = list_campaign_recipes(tmp_path)
    assert result.items == []


# ---------------------------------------------------------------------------
# find_campaign_by_name
# ---------------------------------------------------------------------------


def test_find_campaign_by_name_returns_match(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "my-campaign.yaml",
        _campaign_data(name="my-campaign"),
    )
    result = find_campaign_by_name("my-campaign", tmp_path)
    assert result is not None
    assert result.name == "my-campaign"


def test_find_campaign_by_name_returns_none_when_missing(tmp_path: Path):
    result = find_campaign_by_name("nonexistent", tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# load_campaign_recipes_in_packs
# ---------------------------------------------------------------------------


def test_load_campaign_recipes_in_packs_filters_by_categories(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "impl-campaign.yaml",
        _campaign_data(name="impl-campaign", categories=["implementation-family"]),
    )
    _write_yaml(
        campaigns_dir / "research-campaign.yaml",
        _campaign_data(name="research-campaign", categories=["research-family"]),
    )
    results = load_campaign_recipes_in_packs(frozenset({"implementation-family"}), tmp_path)
    assert len(results) == 1
    assert results[0].name == "impl-campaign"


def test_load_campaign_recipes_in_packs_includes_allowed_recipes(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "special-campaign.yaml",
        _campaign_data(name="special-campaign", categories=["research-family"]),
    )
    results = load_campaign_recipes_in_packs(
        frozenset({"implementation-family"}),
        tmp_path,
        allowed_recipe_names=frozenset({"special-campaign"}),
    )
    assert len(results) == 1
    assert results[0].name == "special-campaign"


# ---------------------------------------------------------------------------
# validate_recipe campaign branch
# ---------------------------------------------------------------------------


def test_validate_recipe_skips_step_check_for_campaign():
    recipe = Recipe(
        name="my-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[CampaignDispatch(name="phase-one", recipe="impl", task="Do it")],
        steps={},
    )
    errors = validate_recipe(recipe)
    assert "Recipe must have at least one step." not in errors


def test_validate_recipe_requires_dispatches_for_campaign():
    recipe = Recipe(
        name="my-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[],
        steps={},
    )
    errors = validate_recipe(recipe)
    assert any("dispatch" in e.lower() for e in errors)


def test_validate_recipe_standard_recipe_still_requires_steps():
    recipe = Recipe(
        name="standard",
        description="test",
        kind=RecipeKind.STANDARD,
        steps={},
    )
    errors = validate_recipe(recipe)
    assert any("step" in e.lower() for e in errors)


def test_bundled_example_campaign_parseable():
    example_path = pkg_root() / "recipes" / "examples" / "example-campaign.yaml"
    recipe = load_recipe(example_path)
    assert recipe.kind == RecipeKind.CAMPAIGN
    assert len(recipe.dispatches) == 2


# ---------------------------------------------------------------------------
# promote-to-main campaign
# ---------------------------------------------------------------------------


def test_promote_to_main_campaign_parseable():
    path = pkg_root() / "recipes" / "campaigns" / "promote-to-main.yaml"
    recipe = load_recipe(path)
    assert recipe.name == "promote-to-main"
    assert recipe.kind == RecipeKind.CAMPAIGN
    assert recipe.recipe_version == "1.0.0"


def test_promote_to_main_campaign_passes_validation():
    path = pkg_root() / "recipes" / "campaigns" / "promote-to-main.yaml"
    recipe = load_recipe(path)
    findings = validate_recipe(recipe)
    assert findings == [], f"Unexpected findings: {findings}"


def test_promote_to_main_campaign_dispatch_chain():
    path = pkg_root() / "recipes" / "campaigns" / "promote-to-main.yaml"
    recipe = load_recipe(path)
    names = [d.name for d in recipe.dispatches]
    assert names == ["full-audit", "review-gate", "build-map", "implement-findings", "promote"]
    gate_dispatches = [d for d in recipe.dispatches if d.gate]
    assert len(gate_dispatches) == 1
    gd = gate_dispatches[0]
    assert gd.name == "review-gate"
    assert gd.gate == DispatchGateType.CONFIRM
    assert gd.message
    assert not gd.recipe
    assert not gd.capture


def test_promote_to_main_campaign_in_list_campaign_recipes(tmp_path: Path):
    result = list_campaign_recipes(tmp_path)
    assert result.errors == []
    names = [r.name for r in result.items]
    assert "promote-to-main" in names


# ---------------------------------------------------------------------------
# research-campaign skeleton
# ---------------------------------------------------------------------------


def test_research_campaign_parseable():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    assert recipe.name == "research-campaign"
    assert recipe.kind == RecipeKind.CAMPAIGN
    assert recipe.recipe_version == "1.0.0"


def test_research_campaign_passes_structural_validation():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    findings = validate_recipe(recipe)
    assert findings == [], f"Unexpected findings: {findings}"


def test_research_campaign_header_fields():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    assert recipe.kind == RecipeKind.CAMPAIGN
    assert recipe.categories == ["research-family"]
    assert recipe.requires_recipe_packs == ["research-family"]
    assert recipe.allowed_recipes == [
        "research-design",
        "research-implement",
        "research-review",
        "research-archive",
    ]
    assert recipe.continue_on_failure is False
    assert recipe.recipe_version == "1.0.0"
    assert recipe.version is None


def test_research_campaign_ingredients_match_research_yaml():
    # research.yaml is the canonical ingredient source for the research family;
    # research-campaign.yaml must expose a subset of those keys so the campaign
    # orchestrator can forward ingredients to each sub-recipe unchanged.
    # If research.yaml is ever renamed, update this test's path accordingly.
    campaign_path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    research_path = pkg_root() / "recipes" / "research.yaml"
    campaign_recipe = load_recipe(campaign_path)
    research_recipe = load_recipe(research_path)
    assert set(campaign_recipe.ingredients.keys()).issubset(
        set(research_recipe.ingredients.keys())
    )
    assert campaign_recipe.ingredients["task"].required is True
    assert campaign_recipe.ingredients["source_dir"].required is True
    assert campaign_recipe.ingredients["issue_url"].required is False
    assert campaign_recipe.ingredients["issue_url"].default is None
    assert campaign_recipe.ingredients["base_branch"].default == "main"
    assert campaign_recipe.ingredients["review_design"].default == "true"
    assert campaign_recipe.ingredients["review_pr"].default == "false"
    assert campaign_recipe.ingredients["audit_claims"].default == "false"
    assert campaign_recipe.ingredients["output_mode"].default == "local"


def test_research_campaign_has_four_dispatches():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    assert len(recipe.dispatches) == 4
    assert recipe.steps == {}


def test_research_campaign_allowed_recipes_kebab_case():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)

    for name in recipe.allowed_recipes:
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name)


def test_research_campaign_dispatch_chain():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    names = [d.name for d in recipe.dispatches]
    assert names == ["run-design", "run-implement", "run-review", "run-archive"]
    assert recipe.dispatches[0].recipe == "research-design"
    assert recipe.dispatches[0].depends_on == []
    assert recipe.dispatches[1].recipe == "research-implement"
    assert recipe.dispatches[1].depends_on == ["run-design"]
    assert recipe.dispatches[2].recipe == "research-review"
    assert recipe.dispatches[2].depends_on == ["run-implement"]
    assert recipe.dispatches[3].recipe == "research-archive"
    assert recipe.dispatches[3].depends_on == ["run-review"]
    for d in recipe.dispatches:
        assert d.task, f"Dispatch {d.name!r} has empty task"
        assert d.gate is None


def test_research_campaign_dispatch_ingredients_are_strings():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    for d in recipe.dispatches:
        for key, val in d.ingredients.items():
            assert isinstance(val, str), (
                f"Dispatch {d.name!r} ingredient {key!r} is {type(val).__name__}, not str"
            )


def test_research_campaign_run_design_capture():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[0]
    assert d.name == "run-design"
    assert set(d.capture.keys()) == {
        "worktree_path",
        "research_dir",
        "experiment_plan",
        "visualization_plan_path",
    }
    for key, val in d.capture.items():
        assert val == f"${{{{ result.{key} }}}}"


def test_research_campaign_run_implement_ingredients():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[1]
    assert d.name == "run-implement"
    assert set(d.ingredients.keys()) == {
        "worktree_path",
        "research_dir",
        "experiment_plan",
        "visualization_plan_path",
        "source_dir",
    }
    assert d.ingredients["worktree_path"] == "${{ campaign.worktree_path }}"
    assert d.ingredients["research_dir"] == "${{ campaign.research_dir }}"
    assert d.ingredients["experiment_plan"] == "${{ campaign.experiment_plan }}"
    assert d.ingredients["visualization_plan_path"] == "${{ campaign.visualization_plan_path }}"
    assert d.ingredients["source_dir"] == "${{ inputs.source_dir }}"


def test_research_campaign_run_implement_capture():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[1]
    assert d.name == "run-implement"
    assert set(d.capture.keys()) == {"report_path"}
    assert d.capture["report_path"] == "${{ result.report_path }}"


def test_research_campaign_run_review_ingredients():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[2]
    assert d.name == "run-review"
    assert set(d.ingredients.keys()) == {
        "worktree_path",
        "research_dir",
        "experiment_plan",
        "visualization_plan_path",
        "report_path",
        "source_dir",
    }
    assert d.ingredients["source_dir"] == "${{ inputs.source_dir }}"
    for key in [
        "worktree_path",
        "research_dir",
        "experiment_plan",
        "visualization_plan_path",
        "report_path",
    ]:
        assert d.ingredients[key] == f"${{{{ campaign.{key} }}}}"


def test_research_campaign_run_review_capture():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[2]
    assert d.name == "run-review"
    assert set(d.capture.keys()) == {"pr_url", "all_diagram_paths", "report_path_after_finalize"}
    assert d.capture["pr_url"] == "${{ result.pr_url }}"
    assert d.capture["all_diagram_paths"] == "${{ result.all_diagram_paths }}"
    assert d.capture["report_path_after_finalize"] == "${{ result.report_path_after_finalize }}"


def test_research_campaign_run_archive_ingredients():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[3]
    assert d.name == "run-archive"
    assert set(d.ingredients.keys()) == {
        "base_branch",
        "worktree_path",
        "research_dir",
        "pr_url",
        "all_diagram_paths",
        "report_path_after_finalize",
    }
    assert d.ingredients["base_branch"] == "${{ inputs.base_branch }}"
    for key in d.ingredients:
        if key == "base_branch":
            continue
        assert d.ingredients[key] == f"${{{{ campaign.{key} }}}}"


def test_research_campaign_no_campaign_refs_in_run_design():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[0]
    for key, val in d.ingredients.items():
        assert "${{ campaign." not in val


def test_research_campaign_run_archive_no_capture():
    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    d = recipe.dispatches[3]
    assert d.name == "run-archive"
    assert d.capture == {}


def test_research_campaign_capture_keys_are_identifiers():
    from autoskillit.recipe.rules.rules_campaign import _IDENT_RE

    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    for d in recipe.dispatches:
        for key in d.capture.keys():
            assert _IDENT_RE.match(key), (
                f"Dispatch {d.name!r} capture key {key!r} is not a valid identifier"
            )


def test_research_campaign_capture_values_reference_result():
    from autoskillit.recipe.rules.rules_campaign import _RESULT_TEMPLATE_RE

    path = pkg_root() / "recipes" / "campaigns" / "research-campaign.yaml"
    recipe = load_recipe(path)
    for d in recipe.dispatches:
        if d.name == "run-archive":
            assert d.capture == {}, f"run-archive must have empty capture, got {d.capture!r}"
            continue
        for val in d.capture.values():
            assert _RESULT_TEMPLATE_RE.match(val.strip()), (
                f"Dispatch {d.name!r} capture value {val!r} does not reference result"
            )


def test_implement_findings_has_model_context_window_ingredient():
    path = pkg_root() / "recipes" / "implement-findings.yaml"
    recipe = load_recipe(path)
    assert "model_context_window" in recipe.ingredients
    ing = recipe.ingredients["model_context_window"]
    assert ing.hidden is True
    assert ing.default == "200000"


def test_full_audit_done_step_emits_csv_format():
    path = pkg_root() / "recipes" / "full-audit.yaml"
    recipe = load_recipe(path)
    done_step = recipe.steps["done"]
    assert (
        "comma-separated" in done_step.message.lower()
        or "comma,separated" in done_step.message.lower()
    )
    assert '["url' not in done_step.message
