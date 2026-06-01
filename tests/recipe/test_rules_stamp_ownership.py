"""Tests for exclusive-stamp-ownership semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe.rules.rules_stamp_ownership as _rso
from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER, Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_ID = "exclusive-stamp-ownership"


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


def test_stamp_ownership_rule_fires_for_non_owner(tmp_path: Path) -> None:
    """Rule fires when a non-owner skill contains the stamp string."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            # bad-skill

            ## Output

            Write the following line at the top of the plan file:
            {DRY_WALKTHROUGH_VERIFIED_MARKER}
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("bad-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_rso, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _RULE_ID in rule_ids
    matching = [f for f in findings if f.rule == _RULE_ID]
    assert matching[0].severity == Severity.ERROR
    assert "bad-skill" in matching[0].message
    assert "dry-walkthrough" in matching[0].message


def test_stamp_ownership_rule_passes_for_owner(tmp_path: Path) -> None:
    """Rule does NOT fire when the owner skill contains its own stamp."""
    skill_dir = tmp_path / "dry-walkthrough"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            # dry-walkthrough

            ## Output

            Write the following line at the top of the plan file:
            {DRY_WALKTHROUGH_VERIFIED_MARKER}
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("dry-walkthrough"))
    recipe = load_recipe(recipe_path)

    with patch.object(_rso, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _RULE_ID not in rule_ids


def test_stamp_ownership_rule_passes_for_backtick_reference(tmp_path: Path) -> None:
    """Rule does NOT fire when the stamp is referenced in backticks (read/check context)."""
    skill_dir = tmp_path / "checker-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            # checker-skill

            ## Steps

            If it does not contain `{DRY_WALKTHROUGH_VERIFIED_MARKER}`:
            - Display warning
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("checker-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_rso, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _RULE_ID not in rule_ids


def test_stamp_ownership_rule_passes_for_unrelated_skill(tmp_path: Path) -> None:
    """Rule does NOT fire when a skill contains no registered stamps."""
    skill_dir = tmp_path / "clean-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # clean-skill

            ## Output

            Write the plan to temp/.
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("clean-skill"))
    recipe = load_recipe(recipe_path)

    with patch.object(_rso, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _RULE_ID not in rule_ids
