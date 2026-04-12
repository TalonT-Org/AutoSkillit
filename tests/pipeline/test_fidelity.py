"""Tests for review-pr fidelity dimension: linked issue extraction and findings.

These tests verify the Python helpers in
autoskillit.execution.pr_analysis, which support the fidelity subagent described
in the review-pr SKILL.md Steps 2.8 and 3.
"""

from __future__ import annotations

from autoskillit.execution.pr_analysis import (
    extract_linked_issues,
    is_valid_fidelity_finding,
)

# ---------------------------------------------------------------------------
# extract_linked_issues — PR body and commit messages
# ---------------------------------------------------------------------------


def test_linked_issue_extraction_closes():
    """'Closes #123' in PR body yields ['123']."""
    result = extract_linked_issues("Closes #123\n\nSome description.")
    assert result == ["123"]


def test_linked_issue_extraction_fixes_commit():
    """'Fixes #456' in commit headline is extracted."""
    result = extract_linked_issues("Fixes #456: update auth handler")
    assert result == ["456"]


def test_linked_issue_extraction_resolves():
    """'Resolves #789' is extracted."""
    result = extract_linked_issues("Resolves #789")
    assert result == ["789"]


def test_linked_issue_extraction_multiple():
    """'Closes #1, Resolves #2' yields both numbers deduplicated."""
    body = "Closes #1\nResolves #2\nSome description."
    result = extract_linked_issues(body)
    assert sorted(result, key=int) == ["1", "2"]
    # Check deduplication: each issue appears once
    assert len(result) == len(set(result))


def test_linked_issue_extraction_deduplication():
    """Same issue referenced twice yields one entry."""
    text = "Closes #42\nFixes #42"
    result = extract_linked_issues(text)
    assert result == ["42"]


def test_linked_issue_extraction_none():
    """PR body with no Closes/Fixes/Resolves refs yields empty list."""
    result = extract_linked_issues("Just a generic PR description.")
    assert result == []


def test_linked_issue_extraction_case_insensitive():
    """Extraction is case-insensitive: CLOSES, fixes, Resolves all match."""
    result = extract_linked_issues("CLOSES #10\nfixes #20\nResolves #30")
    assert sorted(result, key=int) == ["10", "20", "30"]


def test_linked_issue_extraction_commit_concatenation():
    """PR body and commit messages concatenated together yield combined results."""
    pr_body = "Closes #100"
    commits = "Fixes #200"
    combined = f"{pr_body}\n{commits}"
    result = extract_linked_issues(combined)
    assert sorted(result, key=int) == ["100", "200"]


def test_linked_issue_extraction_empty_string():
    """Empty string yields empty list."""
    assert extract_linked_issues("") == []


# ---------------------------------------------------------------------------
# is_valid_fidelity_finding — format validation
# ---------------------------------------------------------------------------


def test_fidelity_finding_format():
    """Fidelity subagent output JSON has dimension='fidelity' and valid severity."""
    finding = {
        "file": "src/auth.py",
        "line": 42,
        "dimension": "fidelity",
        "severity": "critical",
        "message": "Missing requirement: OAuth token refresh not implemented.",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(finding) is True


def test_fidelity_gap_is_actionable():
    """Gap finding has severity='critical' and requires_decision=false."""
    gap = {
        "file": "src/auth.py",
        "line": 10,
        "dimension": "fidelity",
        "severity": "critical",
        "message": "Gap: token refresh flow not addressed.",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(gap) is True


def test_fidelity_drift_is_warning():
    """Drift finding has severity='warning'."""
    drift = {
        "file": "src/logging.py",
        "line": 7,
        "dimension": "fidelity",
        "severity": "warning",
        "message": "Drift: logging config changed with no corresponding issue requirement.",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(drift) is True


def test_fidelity_gap_with_no_file_is_valid():
    """Gap finding with file='' and line=0 is valid (unpostable — summary only)."""
    gap = {
        "file": "",
        "line": 0,
        "dimension": "fidelity",
        "severity": "critical",
        "message": "Gap: entire feature section 'notifications' not implemented.",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(gap) is True


def test_fidelity_finding_wrong_dimension_invalid():
    """Finding with dimension='arch' is not a valid fidelity finding."""
    finding = {
        "file": "src/auth.py",
        "line": 1,
        "dimension": "arch",
        "severity": "critical",
        "message": "some issue",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(finding) is False


def test_fidelity_finding_unknown_severity_invalid():
    """Finding with severity='high' (not critical/warning) is invalid."""
    finding = {
        "file": "src/auth.py",
        "line": 1,
        "dimension": "fidelity",
        "severity": "high",
        "message": "some issue",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(finding) is False


def test_fidelity_skipped_when_no_linked_issues():
    """extract_linked_issues returns [] for PRs with no Closes/Fixes/Resolves refs.

    The fidelity skip condition is len(linked_issues) == 0. This test confirms
    that a PR body without issue references produces an empty list, which is the
    signal the review-pr skill uses to skip fidelity subagent launch.
    """
    pr_body = "This PR adds a minor cleanup, no issue references."
    linked = extract_linked_issues(pr_body)
    assert linked == []
