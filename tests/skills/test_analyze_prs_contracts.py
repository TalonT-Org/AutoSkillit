"""Contract tests for analyze-prs SKILL.md batch branch naming convention."""

from pathlib import Path

import pytest

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/analyze-prs/SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


def test_analyze_prs_batch_branch_uses_pr_batch_prefix(skill_text: str) -> None:
    """Batch branch name must use pr-batch/pr-merge- prefix, not integration/ prefix.

    The permanent integration branch is named 'integration'. Git cannot have
    both a branch named 'integration' and branches named 'integration/*' —
    the slash-prefix requires 'integration' to be a directory, not a file.
    Batch branches must use a disjoint prefix (pr-batch/).

    We require 'pr-batch/pr-merge-' (not just 'pr-batch/') to anchor the check
    to the branch-computation context rather than any incidental occurrence.
    """
    assert "pr-batch/pr-merge-" in skill_text, (
        "analyze-prs must use 'pr-batch/pr-merge-{ts}' naming for integration_branch — "
        "using 'integration/' prefix conflicts with the permanent 'integration' branch"
    )


def test_analyze_prs_does_not_use_integration_slash_prefix(skill_text: str) -> None:
    """SKILL.md must not compute batch branch names with integration/ prefix.

    The original check only caught 'integration/pr-merge'; it would miss other
    integration/-prefixed patterns such as 'integration/batch-...' or
    'integration/run-...'.  Checking for the bare 'integration/' substring
    prohibits all such patterns while still allowing mentions of 'integration'
    (the permanent branch name) without a trailing slash.
    """
    assert "integration/" not in skill_text, (
        "analyze-prs must not use integration/ as a batch branch prefix in any context — "
        "this conflicts with the permanent 'integration' branch in git ref storage"
    )


def test_analyze_prs_json_example_uses_pr_batch_prefix(skill_text: str) -> None:
    """JSON example in SKILL.md must show pr-batch/ prefix."""
    assert '"pr-batch/pr-merge-' in skill_text, (
        "JSON example in SKILL.md must show correct pr-batch/ prefix"
    )
