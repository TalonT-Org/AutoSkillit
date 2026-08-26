"""Per-family focused tests for skill-contract SKILL.md semantic rules.

Covers the five `@semantic_rule` checks that live in
`rules_skill_content_skill_contract.py`:

  - undefined-bash-placeholder
  - output-section-no-markdown-directive
  - executable-field-content-validity
  - source-attribution-directive
  - inline-content-in-subagent-prompt

These tests were relocated verbatim from `tests/recipe/test_rules_skill_content.py`
as part of the #4852 decomposition; no test bodies were edited.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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


# ---------------------------------------------------------------------------
# undefined-bash-placeholder tests
# ---------------------------------------------------------------------------

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
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SYNTHETIC_BAD_SKILL_MD)

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_BAD_SKILL)

    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
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

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "undefined-bash-placeholder" not in rule_ids


# ---------------------------------------------------------------------------
# output-section-no-markdown-directive tests
# ---------------------------------------------------------------------------

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
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
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
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITH_PATTERNS,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert "output-section-no-markdown-directive" not in rule_ids


# ---------------------------------------------------------------------------
# executable-field-content-validity tests
# ---------------------------------------------------------------------------

_EXEC_RULE_ID = "executable-field-content-validity"


def test_executable_field_content_validity_fires_for_missing_criteria(tmp_path: Path) -> None:
    """Rule fires when a V-rule block mentions acquisition without content-validity language."""
    skill_dir = tmp_path / "plan-experiment"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # plan-experiment

            V9: data manifest completeness
              ERROR if source_type: external lacks acquisition.
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("plan-experiment", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _EXEC_RULE_ID in [f.rule for f in findings], (
        "Rule must fire when V9 mentions acquisition but lacks content-validity criteria"
    )


def test_executable_field_content_validity_passes_when_criteria_present(tmp_path: Path) -> None:
    """Rule does NOT fire when V9 mentions acquisition with content-validity language."""
    skill_dir = tmp_path / "plan-experiment"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # plan-experiment

            V9: data manifest completeness
              ERROR if source_type: external lacks acquisition.
              ERROR if acquisition contains placeholder tokens.
              ERROR if acquisition contains unresolved template syntax.
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("plan-experiment", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _EXEC_RULE_ID not in [f.rule for f in findings], (
        "Rule must not fire when V9 contains placeholder/template rejection language"
    )


def test_executable_field_content_validity_ignores_non_executable_skills(tmp_path: Path) -> None:
    """Rule does NOT fire for skills not in _EXECUTABLE_FIELD_SKILLS."""
    skill_dir = tmp_path / "other-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # other-skill

            V9: some rule
              ERROR if field lacks acquisition.
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("other-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _EXEC_RULE_ID not in [f.rule for f in findings], (
        "Rule must not fire for skills not in _EXECUTABLE_FIELD_SKILLS"
    )


def test_executable_field_content_validity_checks_all_v_rules(tmp_path: Path) -> None:
    """Rule checks all V-rules that mention executable fields, not just V9."""
    skill_dir = tmp_path / "plan-experiment"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # plan-experiment

            V1: first rule
              ERROR if something is missing.

            V7: intermediate rule
              ERROR if spec_path is not provided.

            V9: data manifest completeness
              ERROR if acquisition is present.
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill("plan-experiment", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    exec_findings = [f for f in findings if f.rule == _EXEC_RULE_ID]
    # Both V7 (spec_path) and V9 (acquisition) should fire since neither
    # has content-validity language
    assert len(exec_findings) == 2, (
        f"Expected 2 findings (V7 + V9), got {len(exec_findings)}: "
        + "; ".join(f.message for f in exec_findings)
    )


# ---------------------------------------------------------------------------
# source-attribution-directive tests
# ---------------------------------------------------------------------------

_SOURCE_ATTR_RULE_ID = "source-attribution-directive"

_MOCK_MANIFEST_WITH_SOURCE_PIN = {
    "skills": {
        "test-skill": {
            "source_pin_fields": [
                {
                    "field": "task_title",
                    "required_source": "plan file # heading",
                    "prohibited_sources": ["issue title", "issue body"],
                }
            ],
        }
    }
}

_MOCK_MANIFEST_WITHOUT_SOURCE_PIN = {
    "skills": {
        "test-skill": {
            "expected_output_patterns": ["plan_path\\s*=\\s*/.+"],
        }
    }
}


def test_source_attribution_directive_fires_when_missing(tmp_path: Path) -> None:
    """Rule fires when SKILL.md lacks prohibition language for a skill with source_pin_fields."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # Test Skill

            ## Arguments
            None.

            ### Step 1
            Extract the title from plan files.
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_TEST_SKILL)
    recipe = load_recipe(recipe_path)

    with (
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITH_SOURCE_PIN,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _SOURCE_ATTR_RULE_ID in rule_ids, (
        f"Expected '{_SOURCE_ATTR_RULE_ID}' finding when SKILL.md lacks prohibition, "
        f"got: {rule_ids}"
    )


def test_source_attribution_directive_silent_when_present(tmp_path: Path) -> None:
    """Rule does NOT fire when SKILL.md has prohibition language."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # Test Skill

            ## Critical Constraints

            **NEVER:**
            - Use the issue title, issue body, or any closing_issue metadata for
              `task_title` or `## Title`. These MUST come exclusively from plan
              file headings.
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_TEST_SKILL)
    recipe = load_recipe(recipe_path)

    with (
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITH_SOURCE_PIN,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _SOURCE_ATTR_RULE_ID not in rule_ids, (
        f"Rule must not fire when prohibition language is present, got: {rule_ids}"
    )


def test_source_attribution_directive_silent_without_source_pin_fields(
    tmp_path: Path,
) -> None:
    """Rule does NOT fire when skill has no source_pin_fields in manifest."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # Test Skill

            ## Arguments
            None.
            """
        )
    )

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_CALLING_TEST_SKILL)
    recipe = load_recipe(recipe_path)

    with (
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
        patch(
            "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
            return_value=_MOCK_MANIFEST_WITHOUT_SOURCE_PIN,
        ),
    ):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _SOURCE_ATTR_RULE_ID not in rule_ids, (
        f"Rule must not fire when skill has no source_pin_fields, got: {rule_ids}"
    )


def test_source_attribution_directive_rule_registered() -> None:
    """The source-attribution-directive rule must be registered in the rule registry."""
    import autoskillit.recipe.rules.rules_skill_content  # noqa: F401
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule_names = [r.name for r in _RULE_REGISTRY]
    assert "source-attribution-directive" in rule_names
