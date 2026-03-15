"""Tests for sub-recipe dual validation and semantic rules."""

from __future__ import annotations

import textwrap
from pathlib import Path

from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep


def _make_parent_recipe(
    gate_default: str = "false",
    on_success: str = "done",
    on_failure: str = "escalate",
) -> Recipe:
    return Recipe(
        name="test-recipe",
        description="Test",
        ingredients={
            "sprint_mode": RecipeIngredient(
                description="Enable sprint", default=gate_default, hidden=True
            ),
        },
        steps={
            "sprint_entry": RecipeStep(
                sub_recipe="sprint-prefix",
                gate="sprint_mode",
                on_success=on_success,
                on_failure=on_failure,
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )


def _write_sub_recipe(sub_dir: Path, name: str = "sprint-prefix") -> None:
    sub_dir.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(f"""
        name: {name}
        description: Sub recipe
        kitchen_rules:
          - no native tools
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / f"{name}.yaml").write_text(content)


def test_circular_sub_recipe_detected(tmp_path: Path) -> None:
    """Circular sub-recipe reference (A→B→A) produces a semantic ERROR finding."""
    from autoskillit.recipe.registry import run_semantic_rules

    # Recipe A references sub_recipe B, and B references A — cycle
    # Simulate by creating a sub-recipe that references itself (simpler cycle)
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    # sub-recipe that references itself
    self_ref_content = textwrap.dedent("""
        name: self-ref
        description: Self-referential sub-recipe
        kitchen_rules:
          - no native tools
        ingredients:
          self_gate:
            description: Gate
            default: "false"
        steps:
          recurse:
            sub_recipe: self-ref
            gate: self_gate
            on_success: done
    """)
    (sub_dir / "self-ref.yaml").write_text(self_ref_content)

    # Create a recipe referencing self-ref
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={
            "sprint_mode": RecipeIngredient(description="Gate", default="false"),
        },
        steps={
            "sprint_entry": RecipeStep(
                sub_recipe="self-ref",
                gate="sprint_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    ctx = make_validation_context(recipe, project_dir=tmp_path)
    findings = run_semantic_rules(ctx)
    circular_findings = [f for f in findings if f.rule == "circular-sub-recipe"]
    assert circular_findings, "Expected circular-sub-recipe finding but got none"


def test_unknown_sub_recipe_name_detected() -> None:
    """unknown-sub-recipe rule fires when sub_recipe name is not in available_sub_recipes."""
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={
            "sprint_mode": RecipeIngredient(description="Gate", default="false"),
        },
        steps={
            "sprint_entry": RecipeStep(
                sub_recipe="nonexistent-sub",
                gate="sprint_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    ctx = make_validation_context(recipe, available_sub_recipes=frozenset({"other-sub"}))
    findings = run_semantic_rules(ctx)
    unknown_findings = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert unknown_findings, "Expected unknown-sub-recipe finding but got none"
    assert "nonexistent-sub" in unknown_findings[0].message


def test_known_sub_recipe_name_passes() -> None:
    """unknown-sub-recipe rule does not fire for a valid sub_recipe name."""
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={
            "sprint_mode": RecipeIngredient(description="Gate", default="false"),
        },
        steps={
            "sprint_entry": RecipeStep(
                sub_recipe="sprint-prefix",
                gate="sprint_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    ctx = make_validation_context(recipe, available_sub_recipes=frozenset({"sprint-prefix"}))
    findings = run_semantic_rules(ctx)
    unknown_findings = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert not unknown_findings


def test_dual_validation_runs_standalone_and_combined(tmp_path: Path) -> None:
    """load_and_validate with gate=true validates both standalone and combined graphs."""
    from autoskillit.recipe._api import load_and_validate

    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    _write_sub_recipe(sub_dir)

    # Create a project directory structure for load_and_validate
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)

    recipe_content = textwrap.dedent("""
        name: test-recipe
        description: Test
        autoskillit_version: "0.3.0"
        kitchen_rules:
          - "NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash,
             Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator."
        ingredients:
          sprint_mode:
            description: Enable sprint
            default: "false"
            hidden: true
        steps:
          sprint_entry:
            sub_recipe: sprint-prefix
            gate: sprint_mode
            on_success: finish
            on_failure: escalate
          finish:
            action: stop
            message: Done.
          escalate:
            action: stop
            message: Failed.
    """)
    (recipes_dir / "test-recipe.yaml").write_text(recipe_content)

    result = load_and_validate(
        "test-recipe",
        project_dir=tmp_path,
        ingredient_overrides={"sprint_mode": "true"},
    )
    assert "error" not in result
    # Both standalone and combined graphs were validated
    assert result.get("valid") is True, (
        f"Expected valid=True, suggestions: {result.get('suggestions')}"
    )


def test_dual_validation_standalone_errors_surfaced(tmp_path: Path) -> None:
    """Structural error in standalone recipe (from parse) surfaces in validation result."""
    from autoskillit.recipe._api import load_and_validate

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)

    # A recipe with a sub_recipe step but no gate — structural error
    recipe_content = textwrap.dedent("""
        name: test-recipe
        description: Test
        kitchen_rules:
          - no native tools
        ingredients:
          sprint_mode:
            description: Enable sprint
            default: "false"
        steps:
          sprint_entry:
            sub_recipe: sprint-prefix
            on_success: done
    """)
    (recipes_dir / "test-recipe.yaml").write_text(recipe_content)

    result = load_and_validate("test-recipe", project_dir=tmp_path)
    assert result.get("valid") is False, f"Expected valid=False, got {result.get('valid')}"
    assert any("gate" in str(s) for s in result.get("suggestions", [])), (
        f"Expected gate-related suggestion, got: {result.get('suggestions')}"
    )


def test_combined_graph_dataflow_validated(tmp_path: Path) -> None:
    """Semantic rules run on combined graph when gate=true."""
    from autoskillit.recipe._api import load_and_validate

    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    _write_sub_recipe(sub_dir)

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)

    recipe_content = textwrap.dedent("""
        name: test-recipe
        description: Test
        kitchen_rules:
          - no native tools
        ingredients:
          sprint_mode:
            description: Enable sprint
            default: "false"
            hidden: true
        steps:
          sprint_entry:
            sub_recipe: sprint-prefix
            gate: sprint_mode
            on_success: done
    """)
    (recipes_dir / "test-recipe.yaml").write_text(recipe_content)

    result = load_and_validate(
        "test-recipe",
        project_dir=tmp_path,
        ingredient_overrides={"sprint_mode": "true"},
    )
    # When gate=true, semantic rules should have run (result contains suggestions list)
    assert "suggestions" in result
