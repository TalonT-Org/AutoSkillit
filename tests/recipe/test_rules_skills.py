"""Tests for the unknown-skill-command semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(skill_command: str) -> Recipe:
    """Minimal recipe factory for unknown-skill-command rule tests."""
    return Recipe(
        name="test-recipe",
        description="Test recipe for unknown-skill-command rule.",
        version="0.2.0",
        kitchen_rules="Use run_skill only.",
        steps={
            "run": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": skill_command},
            )
        },
    )


def test_unknown_skill_command_flagged() -> None:
    """Skill command referencing a nonexistent skill produces ERROR."""
    recipe = _make_recipe("/autoskillit:nonexistent-skill-xyz")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-skill-command"]
    assert unknown, "Expected unknown-skill-command finding for nonexistent skill"
    assert all(f.severity == Severity.ERROR for f in unknown)
    assert any("nonexistent-skill-xyz" in f.message for f in unknown)


def test_known_bundled_skill_passes() -> None:
    """Skill command referencing a known bundled skill produces no finding."""
    recipe = _make_recipe("/autoskillit:investigate")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-skill-command"]
    assert not unknown, "Known skill 'investigate' must not trigger unknown-skill-command"


def test_dynamic_skill_command_skipped() -> None:
    """Dynamic template expressions fail-open (no finding)."""
    recipe = _make_recipe("/autoskillit:audit-${{ inputs.audit_type }}")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-skill-command"]
    assert not unknown, "Dynamic skill expressions must not trigger unknown-skill-command"


def test_bare_skill_command_skipped() -> None:
    """Non-autoskillit skill commands fail-open (no finding)."""
    recipe = _make_recipe("/review-pr")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-skill-command"]
    assert not unknown, "Bare (non-autoskillit) commands must not trigger unknown-skill-command"


def test_non_skill_step_not_checked() -> None:
    """Steps with tool != run_skill are not checked by this rule."""
    recipe = Recipe(
        name="test-recipe",
        description="Test recipe.",
        version="0.2.0",
        kitchen_rules="test",
        steps={
            "run": RecipeStep(
                tool="run_cmd",
                with_args={"command": "echo hello"},
            )
        },
    )
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-skill-command"]
    assert not unknown, "run_cmd steps must not trigger unknown-skill-command"


def test_get_bundled_skill_names_is_not_lru_cached() -> None:
    """_get_bundled_skill_names must not use @lru_cache (Composition Root fix)."""
    import autoskillit.recipe.rules_skills as mod

    assert not hasattr(mod._get_bundled_skill_names, "cache_clear"), (
        "_get_bundled_skill_names must not be lru_cache-decorated"
    )
    assert not hasattr(mod._get_skill_category_map, "cache_clear"), (
        "_get_skill_category_map must not be lru_cache-decorated"
    )


def test_bundled_skill_names_not_computed_at_import_v2() -> None:
    """Importing rules_skills must not trigger SkillResolver.list_all."""
    import importlib
    import sys
    from unittest.mock import patch

    # Remove cached module so reload is a fresh import
    mod_name = "autoskillit.recipe.rules_skills"
    sys.modules.pop(mod_name, None)

    with patch("autoskillit.workspace.skills.DefaultSkillResolver.list_all") as mock_list:
        importlib.import_module(mod_name)
        mock_list.assert_not_called()


def test_all_bundled_recipes_skill_commands_resolve() -> None:
    """Every skill_command in bundled recipes references a skill that exists on disk."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        skill_findings = [f for f in findings if f.rule == "unknown-skill-command"]
        assert len(skill_findings) == 0, (
            f"Recipe '{yaml_path.stem}' references unknown skill(s): "
            + "; ".join(f.message for f in skill_findings)
        )
