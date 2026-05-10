"""Tests for multi-recipe research campaign dispatch capture propagation (Group J)."""

from __future__ import annotations

import json

import pytest

from tests.fleet._helpers import (
    _make_recipe_info,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _simple_prompt_builder,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _read_state_file(tool_ctx) -> dict:
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}: {state_files}"
    return json.loads(state_files[0].read_text())


def _setup_research_campaign(tool_ctx):
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()

    design_info = _make_recipe_info("research-design")
    repo.add_recipe("research-design", design_info)
    repo.add_full_recipe(
        design_info.path,
        Recipe(
            name="research-design",
            description="Research design phase",
            kind=RecipeKind.STANDARD,
            ingredients={},
        ),
    )

    impl_info = _make_recipe_info("research-implement")
    repo.add_recipe("research-implement", impl_info)
    repo.add_full_recipe(
        impl_info.path,
        Recipe(
            name="research-implement",
            description="Research implement phase",
            kind=RecipeKind.STANDARD,
            ingredients={
                "worktree_path": RecipeIngredient(description="Path to worktree"),
                "research_dir": RecipeIngredient(description="Path to research dir"),
                "research_dir_rel": RecipeIngredient(description="Repo-relative research dir"),
            },
        ),
    )

    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


@pytest.mark.anyio
async def test_design_captured_values_propagate_to_implement_dispatch(tool_ctx, monkeypatch):
    """End-to-end: design dispatch captures propagate to implement dispatch via state file."""
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult

    _setup_research_campaign(tool_ctx)

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **_: L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt/proj",
                "research_dir": "/tmp/wt/proj/.research",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design the research",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "research_dir": "${{ result.research_dir }}",
        },
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is True

    state_data = _read_state_file(tool_ctx)
    assert state_data.get("captured_values") == {
        "worktree_path": "/tmp/wt/proj",
        "research_dir": "/tmp/wt/proj/.research",
    }

    received_ingredients: list[dict] = []

    def _capturing_prompt_builder(**kwargs):
        received_ingredients.append(kwargs.get("ingredients", {}))
        return "prompt"

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement the research",
        ingredients={
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.research_dir }}",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_capturing_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    assert received_ingredients, "prompt_builder was not called"
    assert received_ingredients[0] == {
        "worktree_path": "/tmp/wt/proj",
        "research_dir": "/tmp/wt/proj/.research",
    }


@pytest.mark.anyio
async def test_missing_campaign_ref_returns_fleet_error(tool_ctx):
    """Dispatch with ${{ campaign.worktree_path }} and no prior captures returns fleet_error."""
    from autoskillit.fleet._api import execute_dispatch

    _setup_research_campaign(tool_ctx)

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement with missing ref",
        ingredients={"worktree_path": "${{ campaign.worktree_path }}"},
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is False
    assert result["error"] == "fleet_unknown_ingredient"
    assert "campaign.worktree_path" in result["user_visible_message"]


@pytest.mark.anyio
async def test_partial_capture_propagates_only_captured_keys(tool_ctx, monkeypatch):
    """Design captured only worktree_path; implement requests uncaptured research_dir."""
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult

    _setup_research_campaign(tool_ctx)

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "worktree_path": "/tmp/wt/proj"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design with partial capture",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={"worktree_path": "${{ result.worktree_path }}"},
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is True

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement with missing research_dir",
        ingredients={
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.research_dir }}",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is False
    assert "error" in result


@pytest.mark.anyio
async def test_full_four_step_chain_verifies_complete_data_lineage(tool_ctx, monkeypatch):
    """Run all four research campaign dispatches in sequence and verify
    that every campaign.* reference is resolvable from prior captures.

    This is the integration-level guard against phantom captures: if any
    dispatch emits a sentinel missing a field that a downstream dispatch
    references via campaign.*, this test fails.
    """
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    from ._helpers import (
        _make_recipe_info,
        _no_sleep_quota_checker,
        _noop_quota_refresher,
    )

    # Load the actual campaign and set up in-memory repo with all sub-recipes
    campaign_path = builtin_recipes_dir() / "campaigns" / "research-campaign.yaml"
    campaign = load_recipe(campaign_path)

    from autoskillit.fleet import FleetSemaphore

    repo = InMemoryRecipeRepository()
    for dispatch in campaign.dispatches:
        recipe_path = builtin_recipes_dir() / f"{dispatch.recipe}.yaml"
        actual_recipe = load_recipe(recipe_path)
        info = _make_recipe_info(dispatch.recipe)
        repo.add_recipe(dispatch.recipe, info)
        repo.add_full_recipe(info.path, actual_recipe)

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=4)
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()

    dispatch_names = [d.name for d in campaign.dispatches]
    assert dispatch_names == ["run-design", "run-implement", "run-review", "run-archive"]

    accumulated: dict[str, str] = {}

    # Step 1: run-design
    def _make_design_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt/proj",
                "research_dir": "/tmp/wt/proj/.research",
                "research_dir_rel": ".research",
                "experiment_plan": "/tmp/wt/proj/.research/plan.md",
                "visualization_plan_path": "/tmp/wt/proj/.research/vis.md",
                "scope_report": "/tmp/wt/proj/.research/scope.md",
                "experiment_type": "observational",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_design_result)

    captured_during_design: dict = {}

    def _capture_prompt_builder(**kwargs):
        captured_during_design.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design the research",
        ingredients={
            "task": "do it",
            "issue_url": "",
            "source_dir": "",
            "base_branch": "main",
            "review_design": "false",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "research_dir": "${{ result.research_dir }}",
            "research_dir_rel": "${{ result.research_dir_rel }}",
            "experiment_plan": "${{ result.experiment_plan }}",
            "visualization_plan_path": "${{ result.visualization_plan_path }}",
            "scope_report": "${{ result.scope_report }}",
            "experiment_type": "${{ result.experiment_type }}",
        },
        prompt_builder=_capture_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-design failed: {result}"

    # Verify design captures
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(state_files) == 1
    design_state = json.loads(state_files[0].read_text())
    accumulated.update(design_state.get("captured_values", {}))

    assert accumulated["worktree_path"] == "/tmp/wt/proj"
    assert accumulated["research_dir"] == "/tmp/wt/proj/.research"
    assert accumulated["research_dir_rel"] == ".research"
    assert accumulated["experiment_plan"] == "/tmp/wt/proj/.research/plan.md"
    assert accumulated["experiment_type"] == "observational"

    # Step 2: run-implement
    def _make_implement_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt/proj",
                "report_path": "/tmp/wt/proj/.research/report.md",
                "experiment_results": "/tmp/wt/proj/.research/results.json",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_implement_result)

    captured_during_implement: dict = {}

    def _implement_prompt_builder(**kwargs):
        captured_during_implement.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement the research",
        ingredients={
            "task": "do it",
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.worktree_path }}/${{ campaign.research_dir_rel }}",
            "research_dir_rel": "${{ campaign.research_dir_rel }}",
            "experiment_plan": "${{ campaign.experiment_plan }}",
            "visualization_plan_path": "${{ campaign.visualization_plan_path }}",
            "source_dir": "",
            "base_branch": "main",
            "issue_url": "",
            "output_mode": "pr",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "report_path": "${{ result.report_path }}",
            "experiment_results": "${{ result.experiment_results }}",
        },
        prompt_builder=_implement_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-implement failed: {result}"

    # Verify implement resolved all campaign refs from design captures
    assert captured_during_implement["worktree_path"] == "/tmp/wt/proj"
    assert captured_during_implement["research_dir"] == "/tmp/wt/proj/.research"
    assert captured_during_implement["research_dir_rel"] == ".research"
    assert captured_during_implement["experiment_plan"] == "/tmp/wt/proj/.research/plan.md"
    assert captured_during_implement["visualization_plan_path"] == "/tmp/wt/proj/.research/vis.md"

    # Update accumulated captures
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    impl_state = next(
        (
            f
            for f in state_files
            if json.loads(f.read_text()).get("campaign_name") == "research-implement"
        ),
        None,
    )
    assert impl_state is not None, "research-implement dispatch state file not found"
    impl_data = json.loads(impl_state.read_text())
    accumulated.update(impl_data.get("captured_values", {}))

    assert accumulated["worktree_path"] == "/tmp/wt/proj"
    assert accumulated["report_path"] == "/tmp/wt/proj/.research/report.md"
    assert accumulated["experiment_results"] == "/tmp/wt/proj/.research/results.json"

    # Step 3: run-review (PR mode)
    def _make_review_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "pr_url": "https://github.com/example/repo/pull/123",
                "report_path_after_finalize": "/tmp/wt/proj/.research/final-report.md",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_review_result)

    captured_during_review: dict = {}

    def _review_prompt_builder(**kwargs):
        captured_during_review.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-review",
        task="Review and open PR",
        ingredients={
            "task": "do it",
            "experiment_results": "${{ campaign.experiment_results }}",
            "experiment_type": "${{ campaign.experiment_type }}",
            "scope_report": "${{ campaign.scope_report }}",
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.worktree_path }}/${{ campaign.research_dir_rel }}",
            "experiment_plan": "${{ campaign.experiment_plan }}",
            "visualization_plan_path": "${{ campaign.visualization_plan_path }}",
            "report_path": "${{ campaign.report_path }}",
            "source_dir": "",
            "base_branch": "main",
            "issue_url": "",
            "output_mode": "pr",
            "review_pr": "true",
            "audit_claims": "false",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "pr_url": "${{ result.pr_url }}",
            "report_path_after_finalize": "${{ result.report_path_after_finalize }}",
        },
        prompt_builder=_review_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-review failed: {result}"

    # Verify review resolved all campaign refs from prior captures
    assert captured_during_review["experiment_results"] == "/tmp/wt/proj/.research/results.json"
    assert captured_during_review["experiment_type"] == "observational"
    assert captured_during_review["scope_report"] == "/tmp/wt/proj/.research/scope.md"
    assert captured_during_review["worktree_path"] == "/tmp/wt/proj"
    assert captured_during_review["research_dir"] == "/tmp/wt/proj/.research"
    assert captured_during_review["report_path"] == "/tmp/wt/proj/.research/report.md"

    # Update accumulated captures
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    review_state = next(
        (
            f
            for f in state_files
            if json.loads(f.read_text()).get("campaign_name") == "research-review"
        ),
        None,
    )
    assert review_state is not None, "research-review dispatch state file not found"
    review_data = json.loads(review_state.read_text())
    accumulated.update(review_data.get("captured_values", {}))

    assert accumulated["pr_url"] == "https://github.com/example/repo/pull/123"
    assert accumulated["report_path_after_finalize"] == "/tmp/wt/proj/.research/final-report.md"

    # Step 4: run-archive (verifies pr_url from review is available)
    def _make_archive_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_archive_result)

    captured_during_archive: dict = {}

    def _archive_prompt_builder(**kwargs):
        captured_during_archive.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-archive",
        task="Archive artifacts",
        ingredients={
            "base_branch": "main",
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.worktree_path }}/${{ campaign.research_dir_rel }}",
            "pr_url": "${{ campaign.pr_url }}",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={},
        prompt_builder=_archive_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-archive failed: {result}"

    # Verify archive resolved pr_url from review captures
    assert captured_during_archive["pr_url"] == "https://github.com/example/repo/pull/123"
    assert captured_during_archive["worktree_path"] == "/tmp/wt/proj"
    assert captured_during_archive["research_dir"] == "/tmp/wt/proj/.research"


@pytest.mark.anyio
async def test_cross_phase_paths_are_coherent_when_implement_creates_new_worktree(
    tool_ctx,
    monkeypatch,
):
    """Verify that run-review receives paths all rooted in the same worktree,
    even when run-implement creates a new worktree internally.

    Design phase emits: worktree_path=/tmp/wt-A, research_dir_rel=research/2026-05-10-test
    Implement phase emits: worktree_path=/tmp/wt-B (NEW), report_path=/tmp/wt-B/report.md
    Assert: run-review receives research_dir reconstructed to /tmp/wt-B/research/2026-05-10-test
    """
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    from ._helpers import (
        _make_recipe_info,
        _no_sleep_quota_checker,
        _noop_quota_refresher,
    )

    campaign_path = builtin_recipes_dir() / "campaigns" / "research-campaign.yaml"
    campaign = load_recipe(campaign_path)

    from autoskillit.fleet import FleetSemaphore

    repo = InMemoryRecipeRepository()
    for dispatch in campaign.dispatches:
        recipe_path = builtin_recipes_dir() / f"{dispatch.recipe}.yaml"
        actual_recipe = load_recipe(recipe_path)
        info = _make_recipe_info(dispatch.recipe)
        repo.add_recipe(dispatch.recipe, info)
        repo.add_full_recipe(info.path, actual_recipe)

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=4)
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()

    # Step 1: run-design — emits design worktree (/tmp/wt-A) and relative research path
    def _make_design_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt-A",
                "research_dir": "/tmp/wt-A/research/2026-05-10-test",
                "research_dir_rel": "research/2026-05-10-test",
                "experiment_plan": "/tmp/wt-A/research/2026-05-10-test/plan.md",
                "visualization_plan_path": "/tmp/wt-A/research/2026-05-10-test/vis.md",
                "scope_report": "/tmp/wt-A/research/2026-05-10-test/scope.md",
                "experiment_type": "observational",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_design_result)

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design the research",
        ingredients={
            "task": "do it",
            "issue_url": "",
            "source_dir": "",
            "base_branch": "main",
            "review_design": "false",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "research_dir": "${{ result.research_dir }}",
            "research_dir_rel": "${{ result.research_dir_rel }}",
            "experiment_plan": "${{ result.experiment_plan }}",
            "visualization_plan_path": "${{ result.visualization_plan_path }}",
            "scope_report": "${{ result.scope_report }}",
            "experiment_type": "${{ result.experiment_type }}",
        },
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-design failed: {result}"

    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    design_state = json.loads(state_files[0].read_text())
    design_captures = design_state.get("captured_values", {})

    assert design_captures["worktree_path"] == "/tmp/wt-A"
    assert design_captures["research_dir_rel"] == "research/2026-05-10-test"

    # Step 2: run-implement — internally creates NEW worktree (/tmp/wt-B) and emits it
    # The campaign re-captures worktree_path to update the anchor
    def _make_implement_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt-B",
                "research_dir": "/tmp/wt-A/research/2026-05-10-test",
                "report_path": "/tmp/wt-B/report.md",
                "experiment_results": "/tmp/wt-B/results.json",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_implement_result)

    captured_implement_ingredients: dict = {}

    def _implement_prompt_builder(**kwargs):
        captured_implement_ingredients.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement the research",
        ingredients={
            "task": "do it",
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.research_dir }}",
            "research_dir_rel": "${{ campaign.research_dir_rel }}",
            "experiment_plan": "${{ campaign.experiment_plan }}",
            "visualization_plan_path": "${{ campaign.visualization_plan_path }}",
            "source_dir": "",
            "base_branch": "main",
            "issue_url": "",
            "output_mode": "pr",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "report_path": "${{ result.report_path }}",
            "experiment_results": "${{ result.experiment_results }}",
        },
        prompt_builder=_implement_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-implement failed: {result}"

    # After implement, campaign.worktree_path should be /tmp/wt-B (updated anchor)
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    impl_state = next(
        (
            f
            for f in state_files
            if json.loads(f.read_text()).get("campaign_name") == "research-implement"
        ),
        None,
    )
    impl_data = json.loads(impl_state.read_text())
    impl_captures = impl_data.get("captured_values", {})

    # The anchor must be updated to the implement worktree
    assert impl_captures["worktree_path"] == "/tmp/wt-B"
    assert impl_captures["report_path"] == "/tmp/wt-B/report.md"
    # research_dir_rel remains stable across worktree transitions
    assert "research_dir_rel" not in impl_captures
    # stale research_dir from implement payload must not leak into captures
    assert "research_dir" not in impl_captures

    # Step 3: run-review — receives reconstructed research_dir from updated anchor + relative
    def _make_review_result(**_):
        return L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "pr_url": "https://github.com/example/repo/pull/123",
                "report_path_after_finalize": "/tmp/wt-B/final-report.md",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _make_review_result)

    captured_review_ingredients: dict = {}

    def _review_prompt_builder(**kwargs):
        captured_review_ingredients.update(kwargs.get("ingredients", {}))
        return "prompt"

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-review",
        task="Review and open PR",
        ingredients={
            "task": "do it",
            "experiment_results": "${{ campaign.experiment_results }}",
            "experiment_type": "${{ campaign.experiment_type }}",
            "scope_report": "${{ campaign.scope_report }}",
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.worktree_path }}/${{ campaign.research_dir_rel }}",
            "experiment_plan": "${{ campaign.experiment_plan }}",
            "visualization_plan_path": "${{ campaign.visualization_plan_path }}",
            "report_path": "${{ campaign.report_path }}",
            "source_dir": "",
            "base_branch": "main",
            "issue_url": "",
            "output_mode": "pr",
            "review_pr": "true",
            "audit_claims": "false",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "pr_url": "${{ result.pr_url }}",
            "report_path_after_finalize": "${{ result.report_path_after_finalize }}",
        },
        prompt_builder=_review_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    result = json.loads(raw.to_envelope())
    assert result["success"] is True, f"run-review failed: {result}"

    # All paths must be coherent — rooted in the same worktree (/tmp/wt-B)
    assert captured_review_ingredients["worktree_path"] == "/tmp/wt-B"
    # reconstruction: /tmp/wt-B + research/2026-05-10-test
    assert captured_review_ingredients["research_dir"] == "/tmp/wt-B/research/2026-05-10-test"
    assert captured_review_ingredients["report_path"] == "/tmp/wt-B/report.md"
    assert captured_review_ingredients["experiment_results"] == "/tmp/wt-B/results.json"
