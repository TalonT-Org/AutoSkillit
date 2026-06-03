"""Tests for inline-content-in-subagent-prompt semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "inline-content-in-subagent-prompt"


def _make_recipe_for_skill(skill_name: str) -> str:
    return textwrap.dedent(
        f"""\
        name: test-recipe
        kitchen_rules:
          - "Use run_skill only."
        steps:
          run_impl:
            tool: run_skill
            with:
              skill_command: "/autoskillit:{skill_name}"
            on_success: done
        """
    )


def _make_skill_md_with_blockquote(var: str) -> str:
    return textwrap.dedent(
        f"""\
        # test-skill

        ## Arguments

        `{{worktree_path}}` — worktree

        ### Step 1: Dispatch subagent

        > Review the following diff:
        > {var}
        > Report findings.
        """
    )


@pytest.mark.xfail(
    reason="inline-content-in-subagent-prompt rule not yet implemented (#3636 prerequisite)",
    strict=False,
)
@pytest.mark.parametrize(
    "banned_var",
    [
        "{annotated_diff_content}",
        "{diff_content}",
        "{section_diff_content}",
    ],
)
def test_inline_content_rule_fires_for_banned_var(tmp_path: Path, banned_var: str) -> None:
    """Rule fires WARNING when a blockquoted subagent prompt contains a banned var."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_make_skill_md_with_blockquote(banned_var))

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("test-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == _RULE_NAME]
    assert matching, f"Rule did not fire for banned var {banned_var}"
    assert matching[0].severity == Severity.WARNING


def test_inline_content_rule_silent_when_path_variable_used(tmp_path: Path) -> None:
    """Rule must NOT fire when blockquote uses a path variable instead of content."""
    skill_dir = tmp_path / "path-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_make_skill_md_with_blockquote("{annotated_diff_path}"))

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("path-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    assert _RULE_NAME not in [f.rule for f in findings]


def test_inline_content_rule_silent_for_non_blockquote_usage(tmp_path: Path) -> None:
    """Rule must NOT fire when banned var appears in a code block, not a blockquote."""
    skill_dir = tmp_path / "code-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # code-skill

            ## Arguments

            `{worktree_path}` — worktree

            ### Step 1

            ```bash
            echo "{annotated_diff_content}"
            ```
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("code-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    assert _RULE_NAME not in [f.rule for f in findings]


def test_inline_content_rule_silent_for_non_skill_step(tmp_path: Path) -> None:
    """Rule must NOT fire for run_python steps — only run_skill is checked."""
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        textwrap.dedent(
            """\
            name: test-recipe
            kitchen_rules:
              - "Use run_python only."
            steps:
              compute:
                tool: run_python
                with:
                  callable: some.module.func
                on_success: done
            """
        )
    )
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    assert _RULE_NAME not in [f.rule for f in findings]
