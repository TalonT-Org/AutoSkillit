"""Block-budget regression guards for the pre_queue_gate block."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

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


def _make_block_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal Recipe factory for block rule tests."""
    return Recipe(
        name="synthetic",
        description="synthetic test recipe",
        ingredients={},
        steps=steps,
        kitchen_rules=[],
        version=None,
        experimental=False,
        requires_packs=[],
    )


class TestBlockMcpToolBudget:
    def test_two_mcp_tools_in_pre_queue_gate_fires_error(self):
        step_a = RecipeStep(
            tool="check_repo_merge_state", block="pre_queue_gate", on_success="step_b"
        )
        step_b = RecipeStep(
            tool="check_repo_merge_state", block="pre_queue_gate", on_success="done"
        )
        stop = RecipeStep(action="stop", message="Pre-queue gate complete.")
        recipe = _make_block_recipe({"step_a": step_a, "step_b": step_b, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-mcp-tool-budget"]
        assert len(matching) == 1
        assert matching[0].severity == Severity.ERROR
        assert "pre_queue_gate" in matching[0].message

    def test_one_mcp_tool_in_pre_queue_gate_is_clean(self):
        step_a = RecipeStep(
            tool="check_repo_merge_state", block="pre_queue_gate", on_success="done"
        )
        stop = RecipeStep(action="stop", message="Pre-queue gate complete.")
        recipe = _make_block_recipe({"step_a": step_a, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-mcp-tool-budget"]
        assert len(matching) == 0


class TestBlockGhApiForbidden:
    def test_gh_api_in_pre_queue_gate_fires_error(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "gh api /repos/o/r"},
            block="pre_queue_gate",
            on_success="done",
        )
        stop = RecipeStep(action="stop", message="Pre-queue gate complete.")
        recipe = _make_block_recipe({"step_a": step_a, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-gh-api-forbidden"]
        assert len(matching) == 1
        assert matching[0].severity == Severity.ERROR

    def test_run_cmd_without_gh_api_is_clean(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo ok"},
            block="pre_queue_gate",
            on_success="done",
        )
        stop = RecipeStep(action="stop", message="Pre-queue gate complete.")
        recipe = _make_block_recipe({"step_a": step_a, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-gh-api-forbidden"]
        assert len(matching) == 0


class TestBlockSingleProducer:
    def test_duplicate_capture_key_fires_warning(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo a"},
            block="test_block",
            capture={"shared_key": "a"},
            on_success="step_b",
        )
        step_a.name = "step_a"
        step_b = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo b"},
            block="test_block",
            capture={"shared_key": "b"},
            on_success="done",
        )
        step_b.name = "step_b"
        stop = RecipeStep(action="stop", message="Block single producer test done.")
        recipe = _make_block_recipe({"step_a": step_a, "step_b": step_b, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-single-producer"]
        assert len(matching) == 1
        assert matching[0].severity == Severity.WARNING
        assert "shared_key" in matching[0].message

    def test_distinct_capture_keys_is_clean(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo a"},
            block="test_block",
            capture={"key_a": "a"},
            on_success="step_b",
        )
        step_a.name = "step_a"
        step_b = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo b"},
            block="test_block",
            capture={"key_b": "b"},
            on_success="done",
        )
        step_b.name = "step_b"
        stop = RecipeStep(action="stop", message="Block single producer test done.")
        recipe = _make_block_recipe({"step_a": step_a, "step_b": step_b, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-single-producer"]
        assert len(matching) == 0


class TestBlockEntryExitReachable:
    def test_disconnected_block_steps_fire_warning(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo a"},
            block="test_block",
            on_success="done",
        )
        step_a.name = "step_a"
        step_b = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo b"},
            block="test_block",
            on_success="done",
        )
        step_b.name = "step_b"
        stop = RecipeStep(action="stop", message="Block entry exit test done.")
        recipe = _make_block_recipe({"step_a": step_a, "step_b": step_b, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-entry-exit-reachable"]
        assert len(matching) == 2
        assert all(f.severity == Severity.WARNING for f in matching)

    def test_linear_block_chain_is_clean(self):
        step_a = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo a"},
            block="test_block",
            on_success="step_b",
        )
        step_a.name = "step_a"
        step_b = RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo b"},
            block="test_block",
            on_success="done",
        )
        step_b.name = "step_b"
        stop = RecipeStep(action="stop", message="Block entry exit test done.")
        recipe = _make_block_recipe({"step_a": step_a, "step_b": step_b, "done": stop})
        findings = run_semantic_rules(recipe)
        matching = [f for f in findings if f.rule == "block-entry-exit-reachable"]
        assert len(matching) == 0
