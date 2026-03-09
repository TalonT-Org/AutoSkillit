"""Structural guards for deletion regression detection in merge-pr and review-pr skills.

Validates that both skills have the required sections and instructions for detecting
when a PR reintroduces code that was deliberately deleted from the base branch.
"""

import pytest

from autoskillit.core.paths import pkg_root

SKILLS_ROOT = pkg_root() / "skills"
MERGE_PR_SKILL = SKILLS_ROOT / "merge-pr" / "SKILL.md"
REVIEW_PR_SKILL = SKILLS_ROOT / "review-pr" / "SKILL.md"


@pytest.fixture(scope="module")
def merge_pr_text():
    return MERGE_PR_SKILL.read_text()


@pytest.fixture(scope="module")
def review_pr_text():
    return REVIEW_PR_SKILL.read_text()


# ── merge-pr guards ────────────────────────────────────────────────────────────


def test_merge_pr_has_deletion_regression_scan_step(merge_pr_text):
    """merge-pr must contain a Step 1.5: Deletion Regression Scan section."""
    assert "Step 1.5" in merge_pr_text, (
        "merge-pr must contain a 'Step 1.5: Deletion Regression Scan' section "
        "that runs before any merge attempt"
    )


def test_merge_pr_deletion_scan_uses_merge_base(merge_pr_text):
    """Step 1.5 must compute the branch divergence point via git merge-base."""
    step_15_idx = merge_pr_text.find("Step 1.5")
    assert step_15_idx != -1
    # Find extent of step 1.5 (up to Step 2)
    step_2_idx = merge_pr_text.find("### Step 2", step_15_idx)
    step_15_section = (
        merge_pr_text[step_15_idx:step_2_idx] if step_2_idx != -1 else merge_pr_text[step_15_idx:]
    )
    assert "merge-base" in step_15_section, (
        "Step 1.5 must use 'git merge-base' to determine the PR's branch divergence point"
    )


def test_merge_pr_deletion_scan_uses_diff_filter_D(merge_pr_text):
    """Step 1.5 must detect files deleted from the base branch since the branch point."""
    step_15_idx = merge_pr_text.find("Step 1.5")
    assert step_15_idx != -1
    step_2_idx = merge_pr_text.find("### Step 2", step_15_idx)
    step_15_section = (
        merge_pr_text[step_15_idx:step_2_idx] if step_2_idx != -1 else merge_pr_text[step_15_idx:]
    )
    assert "--diff-filter=D" in step_15_section, (
        "Step 1.5 must use '--diff-filter=D' to enumerate files deleted from the "
        "base branch since the PR's branch point"
    )


def test_merge_pr_deletion_scan_forces_conflict_report_path(merge_pr_text):
    """Step 1.5 must force the conflict report path when regressions are detected."""
    step_15_idx = merge_pr_text.find("Step 1.5")
    assert step_15_idx != -1
    step_2_idx = merge_pr_text.find("### Step 2", step_15_idx)
    step_15_section = (
        merge_pr_text[step_15_idx:step_2_idx] if step_2_idx != -1 else merge_pr_text[step_15_idx:]
    )
    # Must instruct skipping the direct merge and writing a conflict report
    has_conflict_report_route = (
        "conflict report" in step_15_section.lower()
        or "needs_plan=true" in step_15_section
        or "conflict_report_path" in step_15_section
    )
    assert has_conflict_report_route, (
        "Step 1.5 must instruct that detected regressions bypass the direct merge "
        "and force the conflict report path (needs_plan=true)"
    )


def test_merge_pr_conflict_report_has_deletion_regressions_section(merge_pr_text):
    """Conflict report template must include a Deletion Regressions section."""
    assert "Deletion Regressions" in merge_pr_text, (
        "merge-pr conflict report template must include a '## Deletion Regressions' "
        "section listing what was deleted on base and what the PR reintroduces"
    )


def test_merge_pr_step5_documents_deletion_regression_token(merge_pr_text):
    """Step 5 output contract must document the deletion_regression output token."""
    step_5_idx = merge_pr_text.find("Step 5")
    assert step_5_idx != -1, "merge-pr must have a Step 5 output contract section"
    step_5_section = merge_pr_text[step_5_idx:]
    assert "deletion_regression" in step_5_section, (
        "Step 5 output contract must document the 'deletion_regression' output token "
        "so the recipe capture block can extract it"
    )


# ── review-pr guards ──────────────────────────────────────────────────────────


def test_review_pr_has_deletion_context_pre_step(review_pr_text):
    """review-pr must contain a Step 2.5 Deletion Context pre-computation step."""
    assert "Step 2.5" in review_pr_text, (
        "review-pr must contain a 'Step 2.5: Deletion Context Pre-Computation' step "
        "that runs before the parallel audit subagents are spawned"
    )


def test_review_pr_deletion_context_uses_merge_base(review_pr_text):
    """Step 2.5 must derive the merge base to scope deletion history correctly."""
    step_25_idx = review_pr_text.find("Step 2.5")
    assert step_25_idx != -1
    step_3_idx = review_pr_text.find("Step 3", step_25_idx)
    step_25_section = (
        review_pr_text[step_25_idx:step_3_idx]
        if step_3_idx != -1
        else review_pr_text[step_25_idx:]
    )
    assert (
        "merge_base" in step_25_section
        or "merge-base" in step_25_section
        or "merge_base_commit" in step_25_section
    ), (
        "Step 2.5 must derive the merge base commit so deletion history is scoped "
        "to commits since the PR branched (not all history)"
    )


def test_review_pr_deletion_context_uses_diff_filter_D(review_pr_text):
    """Step 2.5 must use --diff-filter=D to enumerate files deleted since branch point."""
    step_25_idx = review_pr_text.find("Step 2.5")
    assert step_25_idx != -1
    step_3_idx = review_pr_text.find("Step 3", step_25_idx)
    step_25_section = (
        review_pr_text[step_25_idx:step_3_idx]
        if step_3_idx != -1
        else review_pr_text[step_25_idx:]
    )
    assert "--diff-filter=D" in step_25_section, (
        "Step 2.5 must use '--diff-filter=D' to find files deleted from the base branch"
    )


def test_review_pr_has_deletion_regression_audit_subagent(review_pr_text):
    """Step 3 must include a deletion_regression audit dimension."""
    step_3_idx = review_pr_text.find("Step 3")
    assert step_3_idx != -1
    step_4_idx = review_pr_text.find("Step 4", step_3_idx)
    step_3_section = (
        review_pr_text[step_3_idx:step_4_idx] if step_4_idx != -1 else review_pr_text[step_3_idx:]
    )
    assert "deletion_regression" in step_3_section, (
        "Step 3 must include a 'deletion_regression' audit dimension as a parallel subagent"
    )


def test_review_pr_deletion_regression_subagent_severity_is_critical(review_pr_text):
    """The deletion_regression subagent must flag findings as critical severity."""
    dr_idx = review_pr_text.find("7. **deletion_regression**")
    assert dr_idx != -1, (
        "Could not find '7. **deletion_regression**' paragraph in review-pr SKILL.md"
    )
    # Look for 'critical' in the surrounding context (within 500 chars)
    context = review_pr_text[dr_idx : dr_idx + 500]
    assert "critical" in context.lower(), (
        "The deletion_regression audit subagent must instruct findings to use "
        "severity='critical' so they appear in actionable_findings"
    )


def test_review_pr_deletion_regression_requires_decision_false(review_pr_text):
    """The deletion_regression subagent must set requires_decision=false."""
    dr_idx = review_pr_text.find("deletion_regression")
    assert dr_idx != -1
    context = review_pr_text[dr_idx : dr_idx + 1500]
    has_false = "requires_decision=false" in context or "requires_decision: false" in context
    assert has_false, (
        "The deletion_regression audit subagent must set requires_decision=false — "
        "deletion regressions are clear bugs with clear fixes, not design trade-offs"
    )


def test_review_pr_dimension_list_includes_deletion_regression(review_pr_text):
    """The audit dimension list in Step 3 must include deletion_regression."""
    assert "deletion_regression" in review_pr_text, (
        "review-pr/SKILL.md dimension list (arch|tests|...) must include 'deletion_regression'"
    )


def test_review_pr_finding_schema_dimension_union_complete(review_pr_text):
    """The finding schema dimension union in Step 3 must include deletion_regression.

    The broad test (test_review_pr_dimension_list_includes_deletion_regression) passes
    because 'deletion_regression' appears in the 7th subagent description. This test
    specifically checks the schema union string in the JSON block to prevent the schema
    from drifting from the dimension list.
    """
    assert "arch|tests|defense|bugs|cohesion|slop|deletion_regression" in review_pr_text, (
        "The finding schema JSON block in Step 3 must include 'deletion_regression' in "
        "the pipe-separated dimension union: "
        "'arch|tests|defense|bugs|cohesion|slop|deletion_regression'"
    )
