"""Contract tests for analyze-prs SKILL.md batch branch naming convention."""

from pathlib import Path

import pytest
import yaml

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/analyze-prs/SKILL.md"
_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


def test_analyze_prs_batch_branch_uses_pr_batch_prefix(skill_text: str) -> None:
    """Batch branch name must use pr-batch/pr-merge- prefix, not develop/ prefix.

    The permanent develop branch is named 'develop'. Git cannot have
    both a branch named 'develop' and branches named 'develop/*' —
    the slash-prefix requires 'develop' to be a directory, not a file.
    Batch branches must use a disjoint prefix (pr-batch/).

    We require 'pr-batch/pr-merge-' (not just 'pr-batch/') to anchor the check
    to the branch-computation context rather than any incidental occurrence.
    """
    assert "pr-batch/pr-merge-" in skill_text, (
        "analyze-prs must use 'pr-batch/pr-merge-{ts}' naming for batch_branch — "
        "using 'develop/' prefix conflicts with the permanent 'develop' branch"
    )


def test_analyze_prs_does_not_use_develop_slash_prefix(skill_text: str) -> None:
    """SKILL.md must not compute batch branch names with develop/ prefix.

    The original check only caught 'integration/pr-merge'; it would miss other
    develop/-prefixed patterns such as 'develop/batch-...' or
    'develop/run-...'.  Checking for the bare 'develop/' substring
    prohibits all such patterns while still allowing mentions of 'develop'
    (the permanent branch name) without a trailing slash.
    """
    assert "develop/" not in skill_text, (
        "analyze-prs must not use develop/ as a batch branch prefix in any context — "
        "this conflicts with the permanent 'develop' branch in git ref storage"
    )


def test_analyze_prs_json_example_uses_pr_batch_prefix(skill_text: str) -> None:
    """JSON example in SKILL.md must show pr-batch/ prefix."""
    assert '"pr-batch/pr-merge-' in skill_text, (
        "JSON example in SKILL.md must show correct pr-batch/ prefix"
    )


def test_analyze_prs_contract_has_merge_queue_data_path() -> None:
    """C-APR-1: analyze-prs contract must declare merge_queue_data_path input."""
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    inputs = raw.get("skills", {}).get("analyze-prs", {}).get("inputs", [])
    names = [inp["name"] for inp in inputs]
    assert "merge_queue_data_path" in names, (
        "analyze-prs contract must have a merge_queue_data_path input entry"
    )


def test_analyze_prs_step_1_5_graphql_batch(skill_text: str) -> None:
    """Step 1.5 must use GraphQL aliases to batch PR queries, not sequential gh CLI calls."""
    assert "graphql" in skill_text.lower() and "alias" in skill_text.lower()
