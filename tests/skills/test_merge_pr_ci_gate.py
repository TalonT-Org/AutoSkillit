"""Guards for the gh-pr-merge CI gate introduced in Part B of issue #289."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_PATH = pkg_root() / "skills_extended" / "merge-pr" / "SKILL.md"


def test_merge_pr_skill_references_gh_pr_merge() -> None:
    """merge-pr SKILL.md must reference 'gh pr merge' for the simple merge path."""
    content = SKILL_PATH.read_text()
    assert "gh pr merge" in content, (
        "merge-pr SKILL.md must document using 'gh pr merge' for the simple PR path — "
        "local git merge bypasses GitHub's required status checks"
    )


def test_merge_pr_skill_references_squash_auto() -> None:
    """merge-pr SKILL.md must reference '--squash --auto' for queued auto-merge."""
    content = SKILL_PATH.read_text()
    assert "--squash --auto" in content, (
        "merge-pr SKILL.md must document '--squash --auto' for queued auto-merge — "
        "'--squash' alone cannot detect a regression that drops '--auto'"
    )


def test_merge_pr_skill_detects_auto_merge_allowed() -> None:
    """merge-pr SKILL.md must detect autoMergeAllowed before choosing merge command."""
    content = SKILL_PATH.read_text()
    assert "autoMergeAllowed" in content, (
        "merge-pr SKILL.md must detect autoMergeAllowed via GraphQL before Step 2 "
        "to choose between --squash --auto and plain --squash"
    )


def test_merge_pr_skill_references_plain_squash_fallback() -> None:
    """merge-pr SKILL.md must document the plain --squash path (without --auto)."""
    content = SKILL_PATH.read_text()
    # Must contain --squash used without --auto as a distinct case.
    # Remove all "--squash --auto" occurrences; --squash must still appear as a standalone path.
    assert "--squash" in content.replace("--squash --auto", ""), (
        "merge-pr SKILL.md must reference plain '--squash' (without --auto) as a "
        "fallback for repos where autoMergeAllowed=false"
    )


def test_merge_pr_skill_has_pre_flight_mergeability_check() -> None:
    """Test 1e: merge-pr SKILL.md must contain a pre-flight mergeability check."""
    content = SKILL_PATH.read_text()
    step19_pos = content.find("### Step 1.9")
    assert step19_pos != -1, "Step 1.9 heading must exist in SKILL.md"
    step2_pos = content.find("### Step 2")
    assert step2_pos != -1, "Step 2 heading must exist in SKILL.md"
    step19_section = content[step19_pos:step2_pos]
    assert "mergeable" in step19_section, (
        "merge-pr SKILL.md Step 1.9 must contain a pre-flight mergeability check"
    )
    merge_cmd_pos = content.find("gh pr merge", step2_pos)
    assert merge_cmd_pos != -1, "gh pr merge command must exist in Step 2 of SKILL.md"
    assert step19_pos < merge_cmd_pos, (
        "Pre-flight mergeability check (Step 1.9) must appear before"
        " the gh pr merge command in Step 2"
    )


def test_merge_pr_skill_has_timeout_output_template() -> None:
    """Test 1f: merge-pr SKILL.md Step 5 output templates must include a timeout case."""
    content = SKILL_PATH.read_text()
    assert "timeout_error" in content, (
        "merge-pr SKILL.md Step 5 output templates must include a timeout case"
    )
    # Verify timeout template has merged=false and timeout_error=true
    timeout_section_match = re.search(
        r'On timeout.*?\{[^}]*"merged":\s*false[^}]*"timeout_error":\s*true[^}]*\}',
        content,
        re.DOTALL,
    )
    assert timeout_section_match is not None, (
        "merge-pr SKILL.md must have a timeout output case with "
        "merged=false and timeout_error=true"
    )
