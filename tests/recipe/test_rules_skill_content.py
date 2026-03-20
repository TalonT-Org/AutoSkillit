"""Tests for the undefined-bash-placeholder semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import autoskillit.recipe.rules_skill_content as _rsc
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

# Minimal recipe YAML that calls a synthetic bad skill with an undefined placeholder.
# The YAML key for skill arguments is `with:` (maps to `with_args` via _parse_recipe).
_SYNTHETIC_BAD_SKILL_MD = textwrap.dedent(
    """\
    # bad-skill
    ## Arguments
    `{plan_path}` — path to plan

    ### Step 1
    ```bash
    git rebase origin/{base_branch}
    ```
    """
)

_RECIPE_CALLING_BAD_SKILL = textwrap.dedent(
    """\
    name: test-recipe
    kitchen_rules:
      - "Use run_skill only."
    ingredients:
      plan_path:
        description: plan path
        required: true
    steps:
      run_impl:
        tool: run_skill
        with:
          skill_command: "/autoskillit:bad-skill ${{{{ inputs.plan_path }}}}"
        on_success: done
    """
)


def test_undefined_bash_placeholder_rule_fires(tmp_path: Path) -> None:
    """
    run_semantic_rules must surface an undefined-bash-placeholder finding when a
    run_skill step calls a skill whose SKILL.md bash block uses an undeclared
    {placeholder}.
    """
    # Write a synthetic bad SKILL.md into a temp skill dir
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SYNTHETIC_BAD_SKILL_MD)

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_BAD_SKILL)

    recipe = load_recipe(recipe_path)

    # Patch the skill resolver to include the synthetic skill dir
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "undefined-bash-placeholder" in rule_ids, (
        f"Expected 'undefined-bash-placeholder' finding, got: {rule_ids}"
    )


def test_valid_skill_passes_placeholder_rule(tmp_path: Path) -> None:
    """
    run_semantic_rules must NOT fire undefined-bash-placeholder for a skill that
    captures the value at runtime using a shell variable.
    """
    skill_dir = tmp_path / "good-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # good-skill
            ## Arguments
            `{plan_path}` — path to plan

            ### Step 1
            ```bash
            CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
            git rebase origin/${CURRENT_BRANCH}
            ```
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        textwrap.dedent(
            """\
            name: test-recipe
            kitchen_rules:
              - "Use run_skill only."
            ingredients:
              plan_path:
                description: plan path
                required: true
            steps:
              run_impl:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:good-skill ${{{{ inputs.plan_path }}}}"
                on_success: done
            """
        )
    )

    recipe = load_recipe(recipe_path)

    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "undefined-bash-placeholder" not in rule_ids
