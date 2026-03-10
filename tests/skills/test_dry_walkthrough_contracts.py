"""Structural contracts for the dry-walkthrough historical regression check step.

Validates that dry-walkthrough/SKILL.md carries the required instructions
for Step 4.5: Historical Regression Check — the git history scan and
GitHub issues cross-reference between Step 4 (Project Rules) and Step 5 (Fix Plan).
"""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def skill_text() -> str:
    skill_path = pkg_root() / "skills" / "dry-walkthrough" / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
    return skill_path.read_text()


@pytest.fixture(scope="module")
def step_45_section(skill_text: str) -> str:
    """Extract only the Step 4.5 section text."""
    step_45_idx = skill_text.find("Step 4.5")
    if step_45_idx == -1:
        pytest.fail("Step 4.5 not found in dry-walkthrough SKILL.md")
    step_5_idx = skill_text.find("### Step 5", step_45_idx)
    if step_5_idx != -1:
        return skill_text[step_45_idx:step_5_idx]
    return skill_text[step_45_idx:]


def test_dry_walkthrough_has_historical_regression_step(skill_text: str) -> None:
    """dry-walkthrough must contain a Step 4.5: Historical Regression Check section."""
    assert "Step 4.5" in skill_text, (
        "dry-walkthrough SKILL.md must contain a 'Step 4.5: Historical Regression Check' "
        "section inserted between Step 4 (Project Rules) and Step 5 (Fix the Plan)"
    )


def test_dry_walkthrough_historical_step_positioned_after_step4(skill_text: str) -> None:
    """Step 4.5 must appear after Step 4 and before Step 5 in the file."""
    step_4_idx = skill_text.find("### Step 4")
    step_45_idx = skill_text.find("Step 4.5")
    step_5_idx = skill_text.find("### Step 5")
    assert step_4_idx != -1 and step_45_idx != -1 and step_5_idx != -1
    assert step_4_idx < step_45_idx < step_5_idx, (
        "Step 4.5 must be positioned after Step 4 and before Step 5 in dry-walkthrough SKILL.md"
    )


def test_dry_walkthrough_historical_step_uses_git_log(step_45_section: str) -> None:
    """Step 4.5 must instruct using git log to scan recent commit history."""
    assert "git log" in step_45_section, (
        "Step 4.5 must instruct using 'git log' to scan recent commits for "
        "fix/revert/remove patterns overlapping with the plan's target files"
    )


def test_dry_walkthrough_historical_step_checks_fix_keywords(step_45_section: str) -> None:
    """Step 4.5 must check commit messages for fix and revert keywords."""
    has_fix = "fix" in step_45_section.lower()
    has_revert = "revert" in step_45_section.lower()
    assert has_fix and has_revert, (
        "Step 4.5 must list 'fix' and 'revert' as commit message keywords to scan for "
        "when detecting potential plan regressions"
    )


def test_dry_walkthrough_historical_step_scoped_to_recent_history(step_45_section: str) -> None:
    """Step 4.5 must scope the git scan to recent history, not all-time history."""
    has_depth_limit = (
        "-100" in step_45_section
        or "100 commit" in step_45_section.lower()
        or "--since" in step_45_section
        or "2 week" in step_45_section.lower()
    )
    assert has_depth_limit, (
        "Step 4.5 must scope the git log scan to a bounded recent history "
        "(e.g., last 100 commits or --since 2 weeks) — unbounded all-history scanning "
        "is too expensive for a lightweight sanity check"
    )


def test_dry_walkthrough_historical_step_scans_github_issues(step_45_section: str) -> None:
    """Step 4.5 must instruct fetching GitHub issues for overlap detection."""
    assert "gh issue" in step_45_section or "github issue" in step_45_section.lower(), (
        "Step 4.5 must instruct fetching GitHub issues (open + recently closed) "
        "to cross-reference against the plan's target files and described changes"
    )


def test_dry_walkthrough_historical_step_handles_closed_issues(step_45_section: str) -> None:
    """Step 4.5 must include recently closed issues in the scan."""
    assert "closed" in step_45_section.lower(), (
        "Step 4.5 must scan recently closed issues — closed issues track patterns "
        "that were deliberately fixed, making them the primary regression signal"
    )


def test_dry_walkthrough_historical_step_gh_auth_guard(step_45_section: str) -> None:
    """Step 4.5 must guard against missing gh authentication before scanning issues."""
    assert "gh auth" in step_45_section or "authenticated" in step_45_section.lower(), (
        "Step 4.5 must check that gh is authenticated before attempting the "
        "GitHub issues scan, and skip gracefully if not"
    )


def test_dry_walkthrough_historical_step_actionable_vs_informational(step_45_section: str) -> None:
    """Step 4.5 must distinguish actionable findings (edit plan) from informational (terminal)."""
    has_actionable = (
        "actionable" in step_45_section.lower() or "warning note" in step_45_section.lower()
    )
    has_informational = "informational" in step_45_section.lower()
    assert has_actionable and has_informational, (
        "Step 4.5 must distinguish between actionable findings (insert warning note "
        "into plan step) and informational findings (report to terminal only under "
        "Historical Context)"
    )


def test_dry_walkthrough_step7_has_historical_context_section(skill_text: str) -> None:
    """Step 7 terminal output template must include a ### Historical Context section."""
    step_7_idx = skill_text.find("### Step 7")
    assert step_7_idx != -1
    step_7_section = skill_text[step_7_idx:]
    assert "Historical Context" in step_7_section, (
        "Step 7 terminal output template must include a '### Historical Context' "
        "section for reporting informational findings collected in Step 4.5"
    )
