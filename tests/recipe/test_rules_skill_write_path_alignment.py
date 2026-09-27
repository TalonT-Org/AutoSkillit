"""Tests for skill-write-path-recipe-alignment semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe.helpers._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.core.types import RecipeSource
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep
from tests._tracked_recipes import tracked_recipe_paths

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "skill-write-path-recipe-alignment"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
        declaration = "write_paths: unrestricted"
    else:
        write_line = f"Write to `{{{{AUTOSKILLIT_TEMP}}}}/{never_path}/output.json`"
        declaration = f"write_paths: ['{{{{AUTOSKILLIT_TEMP}}}}/{never_path}/']"
    return textwrap.dedent(
        f"""\
        ---
        name: {skill_name}
        description: Synthetic alignment fixture.
        {declaration}
        ---
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
    assert "NEVER block declares write scope 'review-pr/'" in rule_findings[0].message


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


@pytest.mark.parametrize(
    ("output_dir", "expected_findings"),
    [
        ("{{AUTOSKILLIT_TEMP}}/bounded/iter_1", 0),
        ("{{AUTOSKILLIT_TEMP}}/bounded-extra/", 1),
        ("{{AUTOSKILLIT_TEMP}}/other/", 1),
    ],
)
def test_recipe_output_dir_stays_inside_declared_skill_boundary(
    tmp_path: Path, output_dir: str, expected_findings: int
) -> None:
    skill_dir = tmp_path / "bounded"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bounded\ndescription: Bounded output.\n"
        "write_paths: ['{{AUTOSKILLIT_TEMP}}/bounded/']\n---\n",
        encoding="utf-8",
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_yaml("bounded", output_dir), encoding="utf-8")
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(load_recipe(recipe_path))
    assert (
        len([finding for finding in findings if finding.rule == _RULE_NAME]) == expected_findings
    )


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
        ---
        name: synthetic-review-pr
        description: Synthetic divergence fixture.
        write_paths: ['{{AUTOSKILLIT_TEMP}}/review-pr/']
        ---
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
    assert "NEVER block declares write scope 'review-pr/'" in rule_findings[0].message


def _alignment_findings(tmp_path: Path, frontmatter: str, output_dir: str) -> list[str]:
    skill_dir = tmp_path / "scoped"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: scoped\ndescription: Scope fixture.\n{frontmatter}---\n# scoped\n",
        encoding="utf-8",
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_yaml("scoped", output_dir), encoding="utf-8")
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(load_recipe(recipe_path))
    return [finding.message for finding in findings if finding.rule == _RULE_NAME]


@pytest.mark.parametrize("frontmatter", ["", "write_paths: []\n", "write_paths: bad\n"])
def test_rule_cannot_verify_output_dir_without_a_valid_scope(
    tmp_path: Path, frontmatter: str
) -> None:
    messages = _alignment_findings(tmp_path, frontmatter, "{{AUTOSKILLIT_TEMP}}/anywhere/")

    assert len(messages) == 1
    assert "cannot verify output_dir: skill lacks a valid write_paths declaration" in messages[0]


@pytest.mark.parametrize("frontmatter", ["write_paths: unrestricted\n", "write_paths: inherit\n"])
def test_unbounded_scopes_do_not_narrow_output_dir(tmp_path: Path, frontmatter: str) -> None:
    assert _alignment_findings(tmp_path, frontmatter, "{{AUTOSKILLIT_TEMP}}/anywhere/") == []


def test_bounded_scope_violation_names_the_declared_write_scope(tmp_path: Path) -> None:
    messages = _alignment_findings(
        tmp_path, "write_paths: ['{{AUTOSKILLIT_TEMP}}/scoped/']\n", "{{AUTOSKILLIT_TEMP}}/other/"
    )

    assert len(messages) == 1
    assert "is outside the skill's declared write scope" in messages[0]


def _bundled_recipe_paths() -> list[Path]:
    paths = sorted(
        tracked_recipe_paths(
            _PROJECT_ROOT,
            source=RecipeSource.BUILTIN,
            scan_dirs=(".",),
        )
    )
    assert paths
    return paths


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
