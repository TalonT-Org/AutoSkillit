"""
Validate that no SKILL.md bash code block uses an undefined {placeholder} token.

A {placeholder} in a bash block must be either:
  - Declared as an ingredient in ## Arguments / ## Ingredients (passed from outside)
  - Assigned as a shell variable in any bash block in the same skill (captured at runtime)

This test provides structural immunity against the class of bug where a {placeholder}
appears in an executable bash block without a defined source, causing the model to guess
the value from ambient context and produce incorrect shell commands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_declared_ingredients,
    shell_vars_assigned,
)
from autoskillit.recipe.rules_skill_content import (
    _PSEUDOCODE_ALLOWLIST as _PROD_PSEUDOCODE_ALLOWLIST,
)

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILL_DIRS = [
    _REPO_ROOT / "src" / "autoskillit" / "skills",
    _REPO_ROOT / "src" / "autoskillit" / "skills_extended",
]

# Allowlist for {placeholder} tokens that are explicitly documented as pseudocode
# substitution patterns — i.e., the skill prose makes the inference unambiguous
# (e.g., "use this command wherever {X} appears", API URL templates, per-iteration
# values where the prose loop structure is clear).
#
# Extends the production allowlist from rules_skill_content to avoid drift.
_PSEUDOCODE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    _PROD_PSEUDOCODE_ALLOWLIST
    | {
        # ── EXISTING: explicit pseudocode documentation ──────────────────────────────
        # plan_name: pseudocode for "extract the plan file's stem from {plan_path}".
        # The skill prose (Step 0 path detection + Step 1 worktree naming) makes the
        # inference unambiguous; the model reliably derives it from the declared {plan_path}.
        ("implement-worktree", "plan_name"),
        ("implement-worktree-no-merge", "plan_name"),
        # ── NAMING INCONSISTENCIES ────────────────────────────────────────────────────
        # audit-impl: bash blocks use {implementation_ref} as an alias for the declared
        # {branch_name} argument; the skill prose clearly establishes the equivalence.
        ("audit-impl", "implementation_ref"),
        # ── GITHUB API PATH TEMPLATES ─────────────────────────────────────────────────
        # {owner}/{repo}/{number}/{pr_number} tokens appear in `gh api repos/{owner}/{repo}/...`
        # URL patterns. The skill prose always shows how to resolve them (gh repo view,
        # gh pr list output, etc.). This is standard documentation convention for REST URLs.
        ("collapse-issues", "repo"),
        ("diagnose-ci", "owner"),
        ("diagnose-ci", "repo"),
        ("diagnose-ci", "job_id"),  # from API response in prior step
        ("issue-splitter", "repo"),
        ("resolve-review", "owner"),
        ("resolve-review", "repo"),
        ("resolve-review", "number"),
        ("resolve-review", "test_command"),  # prose: "Read test_check.command from config"
        ("review-pr", "owner"),
        ("review-pr", "repo"),
        ("review-pr", "pr_number"),
        # ── GRAPHQL FIELD NAMES (false positives) ─────────────────────────────────────
        # These appear inside single-quoted GraphQL query strings as JSON field names, not
        # as bash template placeholders. The {identifier} regex matches them spuriously.
        ("resolve-review", "databaseId"),
        ("resolve-review", "isResolved"),
        # ── RUNTIME-COMPUTED OUTPUT TOKENS ────────────────────────────────────────────
        # These are computed at runtime and used in output commands. The skill prose
        # explicitly describes how they are derived before they appear in bash blocks.
        ("review-pr", "verdict"),  # computed verdict string
        ("review-pr", "summary_markdown"),  # computed review summary
        ("review-pr", "escalation_user_mention"),  # prose: "set escalation_user_mention=..."
        ("resolve-review", "file"),  # per-finding file path from review comments
        ("resolve-merge-conflicts", "file"),  # per-iteration conflicted file path
        # ── PER-ITERATION / LOOP VALUES ──────────────────────────────────────────────
        # Used inside per-issue/per-PR/per-file loops. The prose establishes the loop
        # structure and makes the substitution unambiguous.
        ("collapse-issues", "orig_number"),
        ("collapse-issues", "combined_number"),
        ("collapse-issues", "combined_url"),
        ("issue-splitter", "parent_url"),  # source issue URL, obtained in prior step
        ("issue-splitter", "route"),  # routing label value, from manifest
        ("make-groups", "topic"),  # prose: "git checkout -b feature/{topic}"
        ("merge-pr", "pr_branch"),  # per-PR head branch from gh pr list
        ("merge-pr", "file_path"),  # per-file iteration in deletion check
        ("merge-pr", "symbol_name"),  # per-symbol grep pattern
        ("open-integration-pr", "number"),  # per-PR iteration value
        ("open-integration-pr", "numbers"),  # formatted joined list of PR numbers
        ("open-integration-pr", "timestamp"),  # generated timestamp
        ("open-integration-pr", "new_pr_number"),  # newly created integration PR number
        ("open-integration-pr", "new_pr_url"),  # newly created integration PR URL
        ("open-pr", "plan_path"),  # iterating over plan_paths (singular loop var)
        ("open-pr", "closing_issue"),  # optional arg declared as [closing_issue]
        ("open-pr", "task_title"),  # derived from first heading of plan file
        ("open-pr", "timestamp"),  # generated timestamp for temp file
        ("pipeline-summary", "bug_count"),  # runtime computed count from audit log
        ("pipeline-summary", "date"),  # runtime computed date string
        ("prepare-issue", "body"),
        ("prepare-issue", "description"),
        ("prepare-issue", "issue_number"),
        ("prepare-issue", "issue_type"),
        ("prepare-issue", "keyword-set"),
        ("prepare-issue", "selected_number"),
        ("prepare-issue", "title"),
        ("process-issues", "number"),  # per-issue iteration value
        ("process-issues", "recipe"),  # recipe name from manifest
        ("triage-issues", "number"),  # per-issue iteration value
        ("triage-issues", "recipe"),  # recipe name from manifest
    }
)


def _all_skill_mds() -> list[tuple[str, Path]]:
    result = []
    for skill_dir in _SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for p in sorted(skill_dir.iterdir()):
            if p.is_dir():
                md = p / "SKILL.md"
                if md.exists():
                    result.append((p.name, md))
    return result


_ALL_SKILL_MDS = _all_skill_mds()
assert _ALL_SKILL_MDS, (
    f"No SKILL.md files discovered in {_SKILL_DIRS}. "
    "Check that skill directories exist and contain subdirectories with SKILL.md files."
)


@pytest.mark.parametrize("skill_name,skill_md", _ALL_SKILL_MDS)
def test_no_undefined_bash_placeholders(skill_name: str, skill_md: Path) -> None:
    """
    Every {placeholder} in a SKILL.md bash block must be either declared as an
    ingredient or assigned as a shell variable in the same skill.

    This provides structural immunity against the bug class where an undefined
    placeholder causes the model to guess the value from ambient context.
    """
    content = skill_md.read_text(encoding="utf-8")
    bash_blocks = extract_bash_blocks(content)
    if not bash_blocks:
        pytest.skip(reason="skill has no bash blocks")

    used = extract_bash_placeholders(bash_blocks)
    if not used:
        pytest.skip(reason="skill has no {placeholder} tokens in bash blocks")

    declared = extract_declared_ingredients(content)
    assigned = shell_vars_assigned(bash_blocks)
    defined = declared | assigned

    allowlisted = {name for (sname, name) in _PSEUDOCODE_ALLOWLIST if sname == skill_name}

    undefined = used - defined - allowlisted
    assert not undefined, (
        f"{skill_md.relative_to(_REPO_ROOT)}: bash block uses undefined "
        f"{{placeholder}} syntax: {sorted(undefined)}.\n"
        f"  Declared ingredients: {sorted(declared)}\n"
        f"  Assigned shell vars:  {sorted(assigned)}\n"
        f"Declare the value as an ingredient in ## Arguments, or capture it at "
        f"runtime as a shell variable: VAR=$(command)"
    )
