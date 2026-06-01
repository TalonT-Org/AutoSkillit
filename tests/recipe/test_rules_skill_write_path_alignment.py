"""Tests for skill-write-path-recipe-alignment semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "skill-write-path-recipe-alignment"

_BUNDLED_RECIPES_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"


def _make_recipe_yaml(skill_name: str, output_dir: str) -> str:
    return textwrap.dedent(
        f"""\
        name: test-{skill_name}
        kitchen_rules:
          - "test"
        steps:
          run_step:
            tool: run_skill
            with:
              skill_command: "/autoskillit:{skill_name} branch"
              output_dir: "{output_dir}"
            on_success: done
          done:
            tool: run_cmd
            with:
              cmd: "echo done"
        """
    )


def _make_skill_md(skill_name: str, never_path: str, use_dynamic: bool = False) -> str:
    if use_dynamic:
        write_line = "Write to `${AUTOSKILLIT_ALLOWED_WRITE_PREFIX}/output.json`"
    else:
        write_line = f"Write to `{{{{AUTOSKILLIT_TEMP}}}}/{never_path}/output.json`"
    return textwrap.dedent(
        f"""\
        # {skill_name}

        ## Critical Constraints

        **NEVER:**
        - Create files outside `{{{{AUTOSKILLIT_TEMP}}}}/{never_path}/`

        **ALWAYS:**
        - Do the work

        ## Workflow

        ### Step 1: Do work

        {write_line}
        """
    )


def test_rule_fires_when_skill_md_path_outside_output_dir(tmp_path: Path) -> None:
    """Rule fires ERROR when SKILL.md scope is broader than recipe's iter-scoped output_dir."""
    skill_name = "test-skill-flat"
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_make_skill_md(skill_name, "review-pr", use_dynamic=False))

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_yaml(
            skill_name,
            "{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.review_loop_count }}",
        )
    )
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_findings = [f for f in findings if f.rule == _RULE_NAME]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR
    assert rule_findings[0].step_name == "run_step"


def test_rule_silent_when_paths_aligned(tmp_path: Path) -> None:
    """Rule does NOT fire when SKILL.md uses ${{AUTOSKILLIT_ALLOWED_WRITE_PREFIX}} (dynamic)."""
    skill_name = "test-skill-dynamic"
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_make_skill_md(skill_name, "review-pr", use_dynamic=True))

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_yaml(
            skill_name,
            "{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.review_loop_count }}",
        )
    )
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_findings = [f for f in findings if f.rule == _RULE_NAME]
    assert len(rule_findings) == 0, f"Unexpected findings: {rule_findings}"


def test_rule_silent_when_output_dir_missing() -> None:
    """Rule does NOT fire when the run_skill step has no output_dir."""
    recipe = Recipe(
        name="test-no-output-dir",
        description="No output_dir step.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps={
            "run_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-pr branch",
                },
                on_success="done",
            ),
            "done": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo done"},
            ),
        },
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == _RULE_NAME]
    assert len(rule_findings) == 0, f"Unexpected findings: {rule_findings}"


def test_rule_fires_on_synthetic_pre_fix_divergence(tmp_path: Path) -> None:
    """Rule fires for synthetic fixture replicating old divergent review-pr state.

    Synthetic fixture only — remains valid regardless of SKILL.md edits.
    Old state: NEVER block declares flat review-pr/ scope, recipe uses iter_N/ scoping,
    SKILL.md has no dynamic write path variable.
    """
    skill_name = "synthetic-review-pr"
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    skill_md_content = textwrap.dedent(
        """\
        # synthetic-review-pr

        ## Critical Constraints

        **NEVER:**
        - Create files outside `{{AUTOSKILLIT_TEMP}}/review-pr/`

        **ALWAYS:**
        - Do the work

        ## Workflow

        ### Step 1: Write output

        Save to: `{{AUTOSKILLIT_TEMP}}/review-pr/prior_threads_{pr_number}.json`
        """
    )
    (skill_dir / "SKILL.md").write_text(skill_md_content)

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_yaml(
            skill_name,
            "{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.review_loop_count }}",
        )
    )
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_findings = [f for f in findings if f.rule == _RULE_NAME]
    assert len(rule_findings) == 1, f"Expected 1 finding, got: {rule_findings}"
    assert rule_findings[0].severity == Severity.ERROR


def _bundled_recipe_paths() -> list[Path]:
    return sorted(_BUNDLED_RECIPES_DIR.glob("*.yaml"))


@pytest.mark.parametrize(
    "recipe_path",
    _bundled_recipe_paths(),
    ids=lambda p: p.stem,
)
def test_rule_silent_on_fixed_bundled_recipes(recipe_path: Path) -> None:
    """skill-write-path-recipe-alignment fires zero findings on all bundled recipes.

    Permanent regression guard: catches any future SKILL.md/recipe divergence.
    """
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == _RULE_NAME]
    assert len(rule_findings) == 0, (
        f"Recipe {recipe_path.name} has skill-write-path-recipe-alignment findings: "
        + "\n".join(f"  step={f.step_name}: {f.message}" for f in rule_findings)
    )
