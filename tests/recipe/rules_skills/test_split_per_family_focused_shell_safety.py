"""Per-family focused tests for shell-safety SKILL.md semantic rules.

Covers the six `@semantic_rule` checks that live in
`rules_skill_content_shell_safety.py`:

  - hardcoded-origin-remote
  - blind-git-add-in-skill
  - interpreter-mediated-write-in-skill
  - no-autoskillit-import-in-skill-python-block
  - posix-char-class-in-skill
  - grep-bre-alternation-in-skill
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.rules_skills._helpers import (
    make_recipe_for_skill,
    write_skill_and_run_rules,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# hardcoded-origin-remote tests
# ---------------------------------------------------------------------------


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
        make_recipe_for_skill(
            "origin-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" in [f.rule for f in findings], (
        f"Rule did not fire for: {label!r}"
    )


def test_hardcoded_origin_does_not_fire_with_remote_variable(tmp_path: Path) -> None:
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
        make_recipe_for_skill(
            "remote-var-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
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
        make_recipe_for_skill(
            "resolve-merge-conflicts",
            {"worktree_path": "wt", "plan_path": "plan", "base_branch": "branch"},
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
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
        make_recipe_for_skill(
            "retry-worktree",
            {"plan_path": "plan", "worktree_path": "wt"},
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
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
    recipe_path.write_text(make_recipe_for_skill("comment-skill", {"worktree_path": "wt"}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings]


def test_hardcoded_origin_does_not_fire_for_shell_default_value_expression(tmp_path: Path) -> None:
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
        make_recipe_for_skill(
            "default-val-skill", {"worktree_path": "worktree", "base_branch": "branch"}
        )
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        "Rule fired on ${REMOTE:-origin} shell default-value expression — "
        "the (?<!-) lookbehind in _LITERAL_ORIGIN_RE should guard this pattern"
    )


@pytest.mark.parametrize(
    "skill_name,ingredients",
    [
        ("implement-worktree-no-merge", {"plan_path": "plan"}),
        ("implement-experiment", {"plan_path": "plan"}),
        ("merge-pr", {}),
        ("review-pr", {}),
        ("pipeline-summary", {}),
    ],
)
def test_hardcoded_origin_does_not_fire_on_part_b_fixed_skills(
    tmp_path: Path, skill_name: str, ingredients: dict
) -> None:
    """
    Regression anchor: bundled skills fixed in Part B must NOT trigger hardcoded-origin-remote.

    Uses SKILL_SEARCH_DIRS isolation so the test fails with a clear assertion error
    (not an opaque ENOENT) if the skill is renamed.
    """
    from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

    skill_info = DefaultSkillResolver().resolve(skill_name)
    assert skill_info is not None, f"bundled {skill_name!r} skill not found"
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(skill_info.path.read_bytes())
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill(skill_name, ingredients))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert "hardcoded-origin-remote" not in [f.rule for f in findings], (
        f"hardcoded-origin-remote fired on {skill_name!r} after Part B fix — "
        "check that all literal 'origin' references in bash blocks have been replaced with $REMOTE"
    )


# ---------------------------------------------------------------------------
# no-autoskillit-import-in-skill-python-block tests
# ---------------------------------------------------------------------------

_PKG_RULE_ID = "no-autoskillit-import-in-skill-python-block"


def _write_pkg_skill_and_run(tmp_path: Path, skill_md_content: str):
    return write_skill_and_run_rules(tmp_path, skill_md_content, skill_name="pkg-skill")


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


# ---------------------------------------------------------------------------
# grep-bre-alternation-in-skill tests
# ---------------------------------------------------------------------------

_BRE_RULE_ID = "grep-bre-alternation-in-skill"


def test_grep_bre_alternation_is_flagged(tmp_path: Path) -> None:
    """grep BRE \\| in a bash block must produce a rule finding."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # test-skill

            ### Step 1
            ```bash
            grep -in 'foo\\|bar' some_file.txt
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("test-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _BRE_RULE_ID in [f.rule for f in findings], (
        "Expected rule to fire for grep BRE \\| pattern in bash block"
    )


def test_git_grep_bre_is_excluded(tmp_path: Path) -> None:
    """git log --grep='foo\\|bar' must NOT produce a rule finding (BRE correct for git)."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # test-skill

            ### Step 1
            ```bash
            git log --oneline --grep="fix\\|revert" -- src/
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("test-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _BRE_RULE_ID not in [f.rule for f in findings], (
        "Rule must not fire for --grep= BRE context (git uses BRE for --grep=)"
    )


# ---------------------------------------------------------------------------
# blind-git-add-in-skill tests
# ---------------------------------------------------------------------------

_BLIND_GIT_ADD_RULE_ID = "blind-git-add-in-skill"


def test_blind_git_add_in_skill_flagged(tmp_path: Path) -> None:
    """blind-git-add-in-skill must fire for git add -A in a bash block."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # bad-skill

            ### Step 1
            ```bash
            git -C {worktree_path} add -A && git -C {worktree_path} commit -m "save"
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("bad-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _BLIND_GIT_ADD_RULE_ID]
    assert len(matching) >= 1, (
        f"Expected blind-git-add-in-skill finding, got: {[f.rule for f in findings]}"
    )
    assert matching[0].severity == Severity.ERROR


def test_scoped_git_add_in_skill_allowed(tmp_path: Path) -> None:
    """blind-git-add-in-skill must NOT fire for scoped git add -- <file>."""
    skill_dir = tmp_path / "good-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # good-skill

            ### Step 1
            ```bash
            git add -- somefile.py
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("good-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _BLIND_GIT_ADD_RULE_ID not in [f.rule for f in findings]


def test_git_add_u_in_skill_allowed(tmp_path: Path) -> None:
    """blind-git-add-in-skill must NOT fire for git add -u (update-index is safe)."""
    skill_dir = tmp_path / "update-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # update-skill

            ### Step 1
            ```bash
            git add -u
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("update-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _BLIND_GIT_ADD_RULE_ID not in [f.rule for f in findings]


def test_git_add_all_long_form_flagged(tmp_path: Path) -> None:
    """blind-git-add-in-skill must fire for git add --all."""
    skill_dir = tmp_path / "all-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # all-skill

            ### Step 1
            ```bash
            git add --all
            ```
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("all-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    assert _BLIND_GIT_ADD_RULE_ID in [f.rule for f in findings]


# ---------------------------------------------------------------------------
# interpreter-mediated-write-in-skill tests
# ---------------------------------------------------------------------------

_INTERP_WRITE_RULE_ID = "interpreter-mediated-write-in-skill"


def _write_skill_and_run_rules(tmp_path: Path, skill_md_content: str):
    return write_skill_and_run_rules(tmp_path, skill_md_content, skill_name="interp-skill")


@pytest.mark.parametrize(
    "bash_content",
    [
        "python3 -c \"open(path, 'w').write(data)\"",
        "python3 - <<'EOF'\nPath(x).write_text(y)\nEOF",
        "result=$(python3 -c \"open(x, 'w').write(y)\")",
    ],
    ids=["python3-c-open-w", "heredoc-write-text", "subshell-open-w"],
)
def test_interpreter_mediated_write_fires(tmp_path: Path, bash_content: str) -> None:
    skill_md = textwrap.dedent(
        f"""\
        # interp-skill

        ### Step 1
        ```bash
        {bash_content}
        ```
        """
    )
    findings = _write_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _INTERP_WRITE_RULE_ID in rule_ids


def test_python_block_write_api_fires(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # interp-skill

        ### Step 1
        ```python
        Path(output).write_text(content)
        ```
        """
    )
    findings = _write_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _INTERP_WRITE_RULE_ID in rule_ids


@pytest.mark.parametrize(
    "bash_content",
    [
        'python3 -c "json.load(open(path))"',
        "echo data > path",
        'python3 -c "print(Path(x).read_text())"',
    ],
    ids=["read-only-open", "shell-redirect", "read-text-only"],
)
def test_interpreter_mediated_write_does_not_fire(tmp_path: Path, bash_content: str) -> None:
    skill_md = textwrap.dedent(
        f"""\
        # interp-skill

        ### Step 1
        ```bash
        {bash_content}
        ```
        """
    )
    findings = _write_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _INTERP_WRITE_RULE_ID not in rule_ids


def test_python_block_read_only_does_not_fire(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # interp-skill

        ### Step 1
        ```python
        data = Path(input_path).read_text()
        ```
        """
    )
    findings = _write_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _INTERP_WRITE_RULE_ID not in rule_ids


# ---------------------------------------------------------------------------
# posix-char-class-in-skill tests
# ---------------------------------------------------------------------------

_POSIX_RULE_ID = "posix-char-class-in-skill"


def test_posix_char_class_fires_for_bracket_expression(tmp_path: Path) -> None:
    """POSIX bracket expression in a bash block must trigger the rule."""
    skill_md = textwrap.dedent(
        """\
        # posix-skill

        ### Step 1
        ```bash
        grep -E '[[:space:]]foo[[:space:]]' input.txt
        ```
        """
    )
    findings = write_skill_and_run_rules(tmp_path, skill_md, skill_name="posix-skill")
    rule_ids = [f.rule for f in findings]
    assert _POSIX_RULE_ID in rule_ids, (
        f"Expected '{_POSIX_RULE_ID}' finding for POSIX bracket expression, got: {rule_ids}"
    )


def test_posix_char_class_does_not_fire_for_pcre_only_block(tmp_path: Path) -> None:
    """A bash block without POSIX bracket expressions must NOT trigger the rule."""
    skill_md = textwrap.dedent(
        """\
        # posix-ok-skill

        ### Step 1
        ```bash
        grep -E '[ \\t]foo[ \\t]' input.txt
        ```
        """
    )
    findings = write_skill_and_run_rules(tmp_path, skill_md, skill_name="posix-ok-skill")
    rule_ids = [f.rule for f in findings]
    assert _POSIX_RULE_ID not in rule_ids, (
        f"Rule must not fire when POSIX brackets are absent, got: {rule_ids}"
    )
