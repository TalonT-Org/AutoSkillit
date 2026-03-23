"""Tests for the undefined-bash-placeholder and hardcoded-origin-remote semantic rules."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

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


_MOCK_MANIFEST_WITH_PATTERNS = {
    "skills": {
        "test-skill": {
            "expected_output_patterns": ["plan_path\\s*=\\s*/.+"],
        }
    }
}

_RECIPE_CALLING_TEST_SKILL = textwrap.dedent(
    """\
    name: test-recipe
    kitchen_rules:
      - "Use run_skill only."
    steps:
      run_impl:
        tool: run_skill
        with:
          skill_command: "/autoskillit:test-skill"
        on_success: done
    """
)


def test_output_section_no_markdown_rule_fires_when_directive_missing(tmp_path: Path) -> None:
    """Semantic rule must report a finding for a SKILL.md with expected_output_patterns
    but no no-markdown directive in the output section."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # Test Skill

            ## Output

            Save the plan to `temp/`.

            ```
            plan_path = {absolute_path}
            ```
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_TEST_SKILL)
    recipe = load_recipe(recipe_path)

    with (
        patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITH_PATTERNS,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "output-section-no-markdown-directive" in rule_ids
    matching = [f for f in findings if f.rule == "output-section-no-markdown-directive"]
    assert len(matching) == 1
    assert "test-skill" in matching[0].message


def test_output_section_no_markdown_rule_passes_when_directive_present(tmp_path: Path) -> None:
    """No finding when the no-markdown directive is present above the output fence."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # Test Skill

            ## Output

            > **IMPORTANT:** Emit the structured output tokens as **literal plain text
            > with no markdown formatting on the token names**.

            ```
            plan_path = {absolute_path}
            ```
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_TEST_SKILL)
    recipe = load_recipe(recipe_path)

    with (
        patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITH_PATTERNS,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "output-section-no-markdown-directive" not in rule_ids


# ---------------------------------------------------------------------------
# hardcoded-origin-remote tests
# ---------------------------------------------------------------------------


def _make_recipe_for_skill(skill_name: str, ingredients: dict[str, str]) -> str:
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


@pytest.mark.parametrize(
    "bash_line,label",
    [
        ("git -C {worktree_path} fetch origin", "fetch origin"),
        ("git rebase origin/{base_branch}", "rebase origin/"),
        ("git log --oneline origin/{base_branch}..HEAD", "log origin/"),
        ("git show origin/{base_branch}:{file}", "show origin/"),
        ("git -C {worktree_path} rev-parse --verify origin/{base_branch}", "rev-parse origin/"),
    ],
)
def test_hardcoded_origin_fires_for_git_remote_commands(
    tmp_path: Path, bash_line: str, label: str
) -> None:
    """hardcoded-origin-remote must fire for any literal origin in git remote commands."""
    skill_dir = tmp_path / "origin-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            # origin-skill
            ## Arguments
            `{{worktree_path}}` — worktree path
            `{{base_branch}}` — base branch

            ### Step 1
            ```bash
            {bash_line}
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "origin-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" in [f.rule for f in findings], (
        f"Rule did not fire for: {label!r}"
    )


def test_hardcoded_origin_silent_with_remote_variable(tmp_path: Path) -> None:
    """hardcoded-origin-remote must NOT fire when skill uses $REMOTE variable."""
    skill_dir = tmp_path / "remote-var-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # remote-var-skill
            ## Arguments
            `{worktree_path}` — worktree path
            `{base_branch}` — branch

            ### Step 0
            ```bash
            REMOTE=$(git -C {worktree_path} remote get-url upstream 2>/dev/null \\
                     && echo upstream \\
                     || echo origin)
            git -C {worktree_path} fetch "$REMOTE"
            git -C {worktree_path} rebase "$REMOTE/{base_branch}"
            git -C {worktree_path} log --oneline "$REMOTE/{base_branch}..HEAD"
            git -C {worktree_path} show "$REMOTE/{base_branch}:{file}"
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "remote-var-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "Rule fired unexpectedly on skill using $REMOTE variable"
    )


def test_hardcoded_origin_does_not_fire_on_fixed_resolve_merge_conflicts(tmp_path: Path) -> None:
    """
    Regression anchor: bundled resolve-merge-conflicts must NOT trigger hardcoded-origin-remote
    after Part B fixes the skill to use REMOTE=$(upstream || origin) instead of literal 'origin'.
    """
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "resolve-merge-conflicts",
            {"worktree_path": "wt", "plan_path": "plan", "base_branch": "branch"},
        )
    )
    recipe = load_recipe(recipe_path)
    # No SKILL_SEARCH_DIRS patch — use the real bundled skill
    findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "hardcoded-origin-remote fired on resolve-merge-conflicts after Part B fix — "
        "check that all literal 'origin' references in bash blocks have been replaced with $REMOTE"
    )


def test_hardcoded_origin_does_not_fire_on_fixed_retry_worktree(tmp_path: Path) -> None:
    """
    Regression anchor: bundled retry-worktree must NOT trigger hardcoded-origin-remote
    after Part B fixes the skill to use REMOTE=$(upstream || origin) instead of literal 'origin'.
    """
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "retry-worktree",
            {"plan_path": "plan", "worktree_path": "wt"},
        )
    )
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "hardcoded-origin-remote fired on retry-worktree after Part B fix — "
        "check that all literal 'origin' references in bash blocks have been replaced with $REMOTE"
    )


def test_hardcoded_origin_ignores_comment_lines(tmp_path: Path) -> None:
    """Lines starting with # must not be inspected for literal origin."""
    skill_dir = tmp_path / "comment-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # comment-skill
            ## Arguments
            `{worktree_path}` — path

            ### Step 1
            ```bash
            # In clone-isolated repos origin is file://, use $REMOTE instead
            REMOTE=$(git remote get-url upstream 2>/dev/null && echo upstream || echo origin)
            git fetch "$REMOTE"
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("comment-skill", {"worktree_path": "wt"}))
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings]
