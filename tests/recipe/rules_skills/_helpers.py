"""Shared fixtures for the rules_skill_content per-family test files.

Defines the canonical list of 15 expected rule names and the recipe/skill
scaffolding helpers used across the per-family test files. Sharing these
removes a 4-way duplication of `_make_recipe_for_skill` and the 3 near-identical
"write a synthetic skill + recipe + run rules" helpers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import RuleFinding, run_semantic_rules

EXPECTED_RULE_NAMES: tuple[str, ...] = (
    "undefined-bash-placeholder",
    "hardcoded-origin-remote",
    "blind-git-add-in-skill",
    "interpreter-mediated-write-in-skill",
    "no-autoskillit-import-in-skill-python-block",
    "posix-char-class-in-skill",
    "grep-bre-alternation-in-skill",
    "output-section-no-markdown-directive",
    "skill-no-issue-comments",
    "transition-boundary-anti-confirmation",
    "executable-field-content-validity",
    "reviews-post-requires-input-flag",
    "source-attribution-directive",
    "graphql-query-requires-shell-invocation",
    "inline-content-in-subagent-prompt",
)


def make_recipe_for_skill(skill_name: str, ingredients: dict[str, str]) -> str:
    """Generate minimal recipe YAML invoking the named skill."""
    parts = [
        "name: test-recipe",
        "kitchen_rules:",
        '  - "Use run_skill only."',
    ]
    if ingredients:
        parts.append("ingredients:")
        for k, v in ingredients.items():
            parts.extend([f"  {k}:", f"    description: {v}", "    required: true"])
    args = " ".join("${{{{ inputs." + k + " }}}}" for k in ingredients)
    skill_cmd = f"/autoskillit:{skill_name}"
    if args:
        skill_cmd += f" {args}"
    parts.extend(
        [
            "steps:",
            "  run_impl:",
            "    tool: run_skill",
            "    with:",
            f'      skill_command: "{skill_cmd}"',
            "    on_success: done",
            "",
        ]
    )
    return "\n".join(parts)


def write_skill_and_run_rules(
    tmp_path: Path,
    skill_md_content: str,
    *,
    skill_name: str = "test-skill",
) -> list[RuleFinding]:
    """Write a synthetic SKILL.md + minimal recipe, run rules, return findings.

    Centralises the write-skill-write-recipe-run-rules flow that was previously
    duplicated three times across the per-family test files (with inconsistent
    helper names) under `tests/recipe/rules_skills/`.
    """
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_md_content)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill(skill_name, {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        return run_semantic_rules(recipe)
