"""Structural assertions for the bundled research-campaign.yaml."""

from __future__ import annotations

import re

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import CAMPAIGN_REF_RE, CampaignDispatch, Recipe, RecipeKind

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "campaigns" / "research-campaign.yaml"

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESULT_TMPL_RE = re.compile(r"^\$\{\{\s*result\.\w+\s*\}\}$")


@pytest.fixture(scope="module")
def recipe():
    if not RECIPE_PATH.exists():
        pytest.skip("research-campaign.yaml not yet created")
    return load_recipe(RECIPE_PATH)


def _dispatch_by_name(recipe: Recipe, name: str) -> CampaignDispatch | None:
    return next((d for d in recipe.dispatches if d.name == name), None)


# ---------------------------------------------------------------------------
# Group 2: Kind/Count/Order
# ---------------------------------------------------------------------------


def test_research_campaign_kind(recipe: Recipe) -> None:
    assert recipe.kind == RecipeKind.CAMPAIGN


def test_research_campaign_has_four_dispatches(recipe: Recipe) -> None:
    assert len(recipe.dispatches) == 4


def test_research_campaign_dispatch_names(recipe: Recipe) -> None:
    assert [d.name for d in recipe.dispatches] == [
        "run-design",
        "run-implement",
        "run-review",
        "run-archive",
    ]


# ---------------------------------------------------------------------------
# Group 3: depends_on Chain
# ---------------------------------------------------------------------------


def test_run_design_depends_on_empty(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-design")
    assert d is not None
    assert d.depends_on == []


def test_run_implement_depends_on_design(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-implement")
    assert d is not None
    assert d.depends_on == ["run-design"]


def test_run_review_depends_on_implement(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-review")
    assert d is not None
    assert d.depends_on == ["run-implement"]


def test_run_archive_depends_on_review(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-archive")
    assert d is not None
    assert d.depends_on == ["run-review"]


def test_depends_on_chain_is_strictly_linear(recipe: Recipe) -> None:
    for d in recipe.dispatches:
        assert len(d.depends_on) <= 1, f"{d.name} has {len(d.depends_on)} predecessors"
    seen: dict[str, str] = {}
    for d in recipe.dispatches:
        if d.depends_on:
            pred = d.depends_on[0]
            assert pred not in seen or seen[pred] == d.name, (
                f"Predecessor {pred!r} claimed by both {seen[pred]!r} and {d.name!r}"
            )
            seen[pred] = d.name


# ---------------------------------------------------------------------------
# Group 4: Capture Dict Validation
# ---------------------------------------------------------------------------


def test_run_design_has_non_empty_capture(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-design")
    assert d is not None
    assert d.capture, "run-design capture should not be empty"
    for key in d.capture:
        assert _IDENT_RE.match(key), f"Capture key {key!r} is not a valid identifier"
    for val in d.capture.values():
        assert _RESULT_TMPL_RE.match(val.from_.strip()), (
            f"Capture value {val!r} does not match result template"
        )


def test_run_design_capture_keys(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-design")
    assert d is not None
    assert set(d.capture.keys()) == {
        "worktree_path",
        "research_dir_rel",
        "experiment_plan",
        "visualization_plan_path",
        "scope_report",
        "experiment_type",
    }


def test_run_implement_has_non_empty_capture(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-implement")
    assert d is not None
    assert d.capture, "run-implement capture should not be empty"
    for key in d.capture:
        assert _IDENT_RE.match(key), f"Capture key {key!r} is not a valid identifier"
    for val in d.capture.values():
        assert _RESULT_TMPL_RE.match(val.from_.strip()), (
            f"Capture value {val!r} does not match result template"
        )


def test_run_implement_capture_keys(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-implement")
    assert d is not None
    assert set(d.capture.keys()) == {"worktree_path", "report_path", "experiment_results"}


def test_run_review_has_non_empty_capture(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-review")
    assert d is not None
    assert d.capture, "run-review capture should not be empty"
    for key in d.capture:
        assert _IDENT_RE.match(key), f"Capture key {key!r} is not a valid identifier"
    for val in d.capture.values():
        assert _RESULT_TMPL_RE.match(val.from_.strip()), (
            f"Capture value {val!r} does not match result template"
        )


def test_run_review_capture_keys(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-review")
    assert d is not None
    assert set(d.capture.keys()) == {
        "pr_url",
        "report_path_after_finalize",
    }


def test_run_archive_has_empty_capture(recipe: Recipe) -> None:
    d = _dispatch_by_name(recipe, "run-archive")
    assert d is not None
    assert d.capture == {}


# ---------------------------------------------------------------------------
# Group 5: Campaign Ref Resolution
# ---------------------------------------------------------------------------


def test_all_campaign_refs_resolve_to_ancestor_captures(recipe: Recipe) -> None:
    accumulated_captures: set[str] = set()
    for d in recipe.dispatches:
        for val in d.ingredients.values():
            match = CAMPAIGN_REF_RE.match(val)
            if match:
                ref_name = match.group(1)
                assert ref_name in accumulated_captures, (
                    f"Dispatch {d.name!r} references campaign.{ref_name} "
                    f"but it was not captured by any ancestor dispatch"
                )
        accumulated_captures.update(d.capture.keys())


def test_run_implement_references_run_design_capture(recipe: Recipe) -> None:
    design = _dispatch_by_name(recipe, "run-design")
    implement = _dispatch_by_name(recipe, "run-implement")
    assert design is not None
    assert implement is not None
    design_capture_keys = set(design.capture.keys())
    implement_ref_keys: set[str] = set()
    for val in implement.ingredients.values():
        match = CAMPAIGN_REF_RE.match(val)
        if match:
            implement_ref_keys.add(match.group(1))
    assert implement_ref_keys & design_capture_keys, (
        "run-implement should reference at least one key captured by run-design"
    )


def test_campaign_ref_tracing_on_inline_fixture() -> None:
    recipe = Recipe(
        name="inline-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[
            CampaignDispatch(
                name="phase-a",
                recipe="impl",
                task="Do A",
                ingredients={},
                depends_on=[],
                capture={"output_path": "${{ result.output_path }}"},
            ),
            CampaignDispatch(
                name="phase-b",
                recipe="impl",
                task="Do B",
                ingredients={"input_path": "${{ campaign.output_path }}"},
                depends_on=["phase-a"],
                capture={},
            ),
        ],
    )
    accumulated: set[str] = set()
    for d in recipe.dispatches:
        for val in d.ingredients.values():
            match = CAMPAIGN_REF_RE.match(val)
            if match:
                assert match.group(1) in accumulated
        accumulated.update(d.capture.keys())


def test_campaign_ref_missing_capture_is_detectable() -> None:
    recipe = Recipe(
        name="broken-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[
            CampaignDispatch(
                name="phase-a",
                recipe="impl",
                task="Do A",
                ingredients={},
                depends_on=[],
                capture={"known_key": "${{ result.known_key }}"},
            ),
            CampaignDispatch(
                name="phase-b",
                recipe="impl",
                task="Do B",
                ingredients={"bad_ref": "${{ campaign.missing_key }}"},
                depends_on=["phase-a"],
                capture={},
            ),
        ],
    )
    accumulated: set[str] = set()
    unresolved: list[tuple[str, str]] = []
    for d in recipe.dispatches:
        for val in d.ingredients.values():
            match = CAMPAIGN_REF_RE.match(val)
            if match and match.group(1) not in accumulated:
                unresolved.append((d.name, match.group(1)))
        accumulated.update(d.capture.keys())
    assert len(unresolved) == 1
    assert unresolved[0] == ("phase-b", "missing_key")
