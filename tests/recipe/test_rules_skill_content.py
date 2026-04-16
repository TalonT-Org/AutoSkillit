"""Tests for skill-content semantic rules: undefined-bash-placeholder,
hardcoded-origin-remote, and no-autoskillit-import-in-skill-python-block."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe.rules_skill_content as _rsc
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe")]

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

    Uses SKILL_SEARCH_DIRS isolation: copies the real bundled skill content into tmp_path so
    the test fails with a clear assertion error (not an opaque ENOENT) if the skill is renamed.
    """
    from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

    skill_info = DefaultSkillResolver().resolve("resolve-merge-conflicts")
    assert skill_info is not None, "bundled resolve-merge-conflicts skill not found"
    skill_dir = tmp_path / "resolve-merge-conflicts"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(skill_info.path.read_bytes())
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "resolve-merge-conflicts",
            {"worktree_path": "wt", "plan_path": "plan", "base_branch": "branch"},
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "hardcoded-origin-remote fired on resolve-merge-conflicts after Part B fix — "
        "check that all literal 'origin' references in bash blocks have been replaced with $REMOTE"
    )


def test_hardcoded_origin_does_not_fire_on_fixed_retry_worktree(tmp_path: Path) -> None:
    """
    Regression anchor: bundled retry-worktree must NOT trigger hardcoded-origin-remote
    after Part B fixes the skill to use REMOTE=$(upstream || origin) instead of literal 'origin'.

    Uses SKILL_SEARCH_DIRS isolation: copies the real bundled skill content into tmp_path so
    the test fails with a clear assertion error (not an opaque ENOENT) if the skill is renamed.
    """
    from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

    skill_info = DefaultSkillResolver().resolve("retry-worktree")
    assert skill_info is not None, "bundled retry-worktree skill not found"
    skill_dir = tmp_path / "retry-worktree"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(skill_info.path.read_bytes())
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "retry-worktree",
            {"plan_path": "plan", "worktree_path": "wt"},
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
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


def test_hardcoded_origin_silent_for_shell_default_value_expression(tmp_path: Path) -> None:
    """hardcoded-origin-remote must NOT fire for ${REMOTE:-origin} shell default-value syntax.

    In `${REMOTE:-origin}`, 'origin' is the fallback in a parameter expansion, not a
    hardcoded literal. The char immediately before 'origin' is '-' (from ':-'), which is
    now guarded by the (?<!-) lookbehind in _LITERAL_ORIGIN_RE.
    """
    skill_dir = tmp_path / "default-val-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # default-val-skill
            ## Arguments
            `{worktree_path}` — worktree path
            `{base_branch}` — branch

            ### Step 0
            ```bash
            git -C {worktree_path} fetch "${REMOTE:-origin}" "{base_branch}"
            git -C {worktree_path} rebase "${REMOTE:-origin}/{base_branch}"
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        _make_recipe_for_skill(
            "default-val-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "Rule fired on ${REMOTE:-origin} shell default-value expression — "
        "the (?<!-) lookbehind in _LITERAL_ORIGIN_RE should guard this pattern"
    )


# ---------------------------------------------------------------------------
# no-autoskillit-import-in-skill-python-block tests  (SC-PKG-1 – SC-PKG-7)
# ---------------------------------------------------------------------------

_PKG_RULE_ID = "no-autoskillit-import-in-skill-python-block"


def _write_pkg_skill_and_run(tmp_path: Path, skill_md_content: str) -> list[object]:
    """Write a synthetic skill SKILL.md and a minimal recipe calling it, then run rules."""
    skill_name = "pkg-skill"
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_md_content)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_make_recipe_for_skill(skill_name, {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_rsc, "SKILL_SEARCH_DIRS", [tmp_path]):
        return run_semantic_rules(recipe)


def test_no_autoskillit_import_fires_for_from_import(tmp_path: Path) -> None:
    """SC-PKG-1: `from autoskillit.foo import bar` in a python3 -c block triggers the rule."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        python3 -c "
        from autoskillit.pipeline.tokens import DefaultTokenLog
        print(DefaultTokenLog())
        "
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID in [f.rule for f in findings], (
        "Expected rule to fire for 'from autoskillit...' import in python3 -c block"
    )


def test_no_autoskillit_import_fires_for_heredoc_form(tmp_path: Path) -> None:
    """SC-PKG-2: heredoc `python3 - <<'EOF'...EOF` form triggers the rule."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        python3 - <<'EOF'
        from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter
        print(TelemetryFormatter())
        EOF
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID in [f.rule for f in findings], (
        "Expected rule to fire for heredoc python3 block with autoskillit import"
    )


def test_no_autoskillit_import_fires_for_bare_import(tmp_path: Path) -> None:
    """SC-PKG-3: bare `import autoskillit` on its own line triggers the rule."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        python3 -c "
        import autoskillit
        print(autoskillit.__version__)
        "
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID in [f.rule for f in findings], (
        "Expected rule to fire for bare 'import autoskillit' in python3 block"
    )


def test_no_autoskillit_import_fires_for_dunder_import(tmp_path: Path) -> None:
    """SC-PKG-4: `__import__('autoskillit' + '.foo', fromlist=[''])` string form triggers."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        python3 -c "
        mod = __import__('autoskillit' + '.execution.github', fromlist=[''])
        "
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID in [f.rule for f in findings], (
        "Expected rule to fire for __import__('autoskillit'...) string form"
    )


def test_no_autoskillit_import_silent_for_stdlib_only(tmp_path: Path) -> None:
    """SC-PKG-5: stdlib-only python3 -c block does NOT trigger the rule."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        python3 -c "import json, sys; print(json.dumps({}))"
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID not in [f.rule for f in findings], (
        "Rule must not fire for stdlib-only python3 block"
    )


def test_no_autoskillit_import_silent_for_no_python_blocks(tmp_path: Path) -> None:
    """SC-PKG-6: SKILL.md with no python3 blocks does NOT trigger the rule."""
    skill_md = textwrap.dedent(
        """\
        # pkg-skill

        ### Step 1
        ```bash
        echo "hello world"
        git status
        ```
        """
    )
    findings = _write_pkg_skill_and_run(tmp_path, skill_md)
    assert _PKG_RULE_ID not in [f.rule for f in findings], (
        "Rule must not fire when no python3 blocks are present"
    )


def test_no_autoskillit_import_zero_findings_on_bundled_recipes() -> None:
    """SC-PKG-7: merge-prs.yaml must yield zero no-autoskillit-import findings.

    All violations in bundled skills (open-integration-pr, review-pr, analyze-prs)
    have been resolved by Part C — python3 blocks replaced with stdlib file-reads."""
    from autoskillit.recipe.io import builtin_recipes_dir  # noqa: PLC0415

    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    pkg_findings = [f for f in findings if f.rule == _PKG_RULE_ID]
    assert len(pkg_findings) == 0, (
        f"Expected zero findings for {_PKG_RULE_ID!r}, got {len(pkg_findings)}: "
        + "; ".join(f.message for f in pkg_findings)
    )
