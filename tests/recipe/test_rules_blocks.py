"""Block-budget regression guards for the pre_queue_gate block."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_QUEUE_CAPABLE = ("implementation.yaml", "remediation.yaml", "implementation-groups.yaml")


@pytest.mark.parametrize("recipe_name", _QUEUE_CAPABLE)
def test_pre_queue_gate_block_contains_at_most_one_run_cmd(recipe_name):
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    block = next((b for b in recipe.blocks if b.name == "pre_queue_gate"), None)
    assert block is not None, f"{recipe_name} must declare pre_queue_gate block anchors"
    run_cmd_count = block.tool_counts.get("run_cmd", 0)
    assert run_cmd_count <= 1, (
        f"{recipe_name}: pre_queue_gate contains {run_cmd_count} run_cmd steps; "
        f"consolidate into a single MCP tool (check_repo_merge_state)."
    )


@pytest.mark.parametrize("recipe_name", _QUEUE_CAPABLE)
def test_pre_queue_gate_block_has_zero_gh_api_shell_patterns(recipe_name):
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    block = next((b for b in recipe.blocks if b.name == "pre_queue_gate"), None)
    assert block is not None, f"{recipe_name} must declare pre_queue_gate block anchors"
    assert block.gh_api_occurrences == 0, (
        f"{recipe_name}: pre_queue_gate has {block.gh_api_occurrences} 'gh api' shell "
        f"invocations; MCP tools must own all GitHub API calls in declared blocks."
    )


@pytest.mark.parametrize("recipe_name", _QUEUE_CAPABLE)
def test_pre_queue_gate_block_produces_three_captures_from_one_step(recipe_name):
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    block = next((b for b in recipe.blocks if b.name == "pre_queue_gate"), None)
    assert block is not None, f"{recipe_name} must declare pre_queue_gate block anchors"
    producers = {
        cap: [s.name for s in block.members if cap in (s.capture or {})]
        for cap in ("queue_available", "merge_group_trigger", "auto_merge_available")
    }
    for cap, producer_names in producers.items():
        assert len(producer_names) == 1, (
            f"{recipe_name}: capture {cap!r} produced by {len(producer_names)} steps "
            f"(expected exactly 1): {producer_names}"
        )
    unique_producers = {p[0] for p in producers.values()}
    assert len(unique_producers) == 1, (
        f"{recipe_name}: three captures come from {len(unique_producers)} steps; "
        f"expected single-producer consolidation (check_repo_merge_state)."
    )


def _make_synthetic_recipe_with_two_gh_api_run_cmds_in_block(block_name: str):
    """Build a minimal Recipe with two run_cmd gh api steps in a declared block.

    Used by the synthetic block budget test below.
    """
    from autoskillit.recipe.schema import Recipe, RecipeStep

    step_a = RecipeStep(
        tool="run_cmd",
        with_args={"cmd": "gh api /repos/o/r/pulls"},
        block=block_name,
        on_success="step_b",
    )
    step_b = RecipeStep(
        tool="run_cmd",
        with_args={"cmd": "gh api graphql -f query='...' "},
        block=block_name,
    )
    return Recipe(
        name="synthetic",
        description="synthetic test recipe",
        ingredients={},
        steps={"step_a": step_a, "step_b": step_b},
        kitchen_rules=[],
        version=None,
        experimental=False,
        requires_packs=[],
    )


def test_synthetic_block_with_two_run_cmds_emits_block_run_cmd_budget_finding():
    """Independent verification: a minimal synthetic recipe with two run_cmd steps
    in a declared block must produce exactly one block-run-cmd-budget finding from
    run_semantic_rules."""
    recipe = _make_synthetic_recipe_with_two_gh_api_run_cmds_in_block("test_block")
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == "block-run-cmd-budget"]
    assert len(matching) == 1
    assert "test_block" in matching[0].message
