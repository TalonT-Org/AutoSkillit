"""Tests that CLAUDE.md (effective content) contains required critical rules and documentation.

The fixture resolves the `@AGENTS.md` import at the top of CLAUDE.md so these tests
see the effective instruction surface an agent reads at session start. Universal
rule bodies live in AGENTS.md and are inherited by CLAUDE.md through that include.

Encodes behavioral contracts derived from friction analysis (issue #250):
- FRICT-1B-3: set_project_path initialization rule in §3.3 (shared, in AGENTS.md)
- FRICT-3A-1: pre-commit critical rule in §3.1 (shared, in AGENTS.md, inherited by CLAUDE.md)
- FRICT-5-2: session diagnostics hyphen path convention documented
- FRICT-7-1: session diagnostics under a dedicated heading, not trailing paragraph
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def claude_md() -> str:
    text = (REPO_ROOT / "CLAUDE.md").read_text()
    # Resolve @AGENTS.md include so tests see the effective content agents see.
    agents_path = REPO_ROOT / "AGENTS.md"
    if "@AGENTS.md" in text and agents_path.exists():
        text = text.replace("@AGENTS.md", agents_path.read_text())
    return text


def test_claude_md_critical_rules_require_precommit(claude_md: str) -> None:
    """CLAUDE.md (effective) must include a pre-commit run rule via @AGENTS.md.

    Pre-commit hook failures caused ~15 friction events across 15 sessions.
    The shared rule lives in AGENTS.md §3.1; CLAUDE.md inherits it through the
    `@AGENTS.md` include at the top of the file (FRICT-3A-1).
    """
    assert "pre-commit run --all-files" in claude_md, (
        "Effective CLAUDE.md must include 'pre-commit run --all-files' — "
        "the shared rule lives in AGENTS.md §3.1 and CLAUDE.md inherits it "
        "via the @AGENTS.md include (FRICT-3A-1)."
    )


def test_claude_md_session_diagnostics_has_dedicated_heading(claude_md: str) -> None:
    """Session diagnostics must have a dedicated ## section heading, not a trailing paragraph.

    A trailing paragraph after the architecture tree is easy to miss. A named
    section (## **7. Session Diagnostics**) is findable by search and TOC
    navigation (FRICT-7-1).
    """
    lines = claude_md.splitlines()
    heading_lines = [
        line
        for line in lines
        if line.startswith("## ")
        and ("session" in line.lower() or "diagnostics" in line.lower() or "log" in line.lower())
    ]
    assert heading_lines, (
        "CLAUDE.md must have a dedicated ## section heading for session diagnostics "
        "(e.g., '## **7. Session Diagnostics**'). Currently it is only a trailing "
        "paragraph after the architecture tree, making it hard to find (FRICT-7-1)."
    )


def test_claude_md_session_diagnostics_mentions_hyphen_convention(claude_md: str) -> None:
    """Session diagnostics section must clarify that path components use hyphens not underscores.

    Session 'f9170655' failed due to underscore vs hyphen mismatch when constructing
    log paths. The convention must be documented explicitly (FRICT-5-2).
    """
    assert "hyphen" in claude_md.lower(), (
        "CLAUDE.md session diagnostics must clarify that path components (log "
        "directory names, session folder names) use hyphens, not underscores "
        "(FRICT-5-2). Without this, agents construct wrong paths."
    )


def test_claude_md_no_stale_fidelity_reference(claude_md: str) -> None:
    """CLAUDE.md pipeline/ section must not list fidelity.py — it does not exist (P2-5).

    The module was folded into execution/pr_analysis.py during refactor bcafe54f.
    The correct documentation is already present at the execution/pr_analysis.py entry.
    A stale reference misleads agents into searching for a file that does not exist.
    """
    assert "fidelity.py" not in claude_md, (
        "CLAUDE.md references 'fidelity.py' under pipeline/ but this module does not exist. "
        "The helpers extract_linked_issues and is_valid_fidelity_finding live in "
        "execution/pr_analysis.py. Remove the stale pipeline/fidelity.py entry (P2-5)."
    )


def test_claude_md_has_github_api_discipline(claude_md: str) -> None:
    """Effective CLAUDE.md must include the GitHub API Call Discipline rule.

    The shared rule lives in AGENTS.md §3.3 and is inherited by CLAUDE.md
    through the `@AGENTS.md` include.
    """
    assert "GitHub API Call Discipline" in claude_md
    assert "sleep 1" in claude_md or "asyncio.sleep(1)" in claude_md


def test_claude_md_contains_commit_discipline_rule(claude_md: str) -> None:
    """CLAUDE.md must contain the commit discipline rule for headless session inheritance."""
    assert "commit discipline" in claude_md.lower() or (
        "--amend" in claude_md and "never" in claude_md.lower()
    )
