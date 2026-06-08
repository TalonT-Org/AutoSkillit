"""Tests that CLAUDE.md and AGENTS.md contain required critical rules.

Encodes behavioral contracts derived from friction analysis (issue #250):
- FRICT-1B-3: set_project_path initialization rule in AGENTS.md §3.3
- FRICT-3A-1: pre-commit critical rule in AGENTS.md §3.1 (shared, not Claude-only)
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
    return (REPO_ROOT / "CLAUDE.md").read_text()


@pytest.fixture()
def agents_md() -> str:
    path = REPO_ROOT / "AGENTS.md"
    assert path.exists(), f"AGENTS.md not found at {path}"
    return path.read_text()


def _section_bounds(lines: list[str], heading_substring: str) -> tuple[int | None, int | None]:
    """Locate a `##` or `###` section in the AGENTS.md tree.

    AGENTS.md uses a `### **3.1. Code and Implementation**` sub-heading rather than a
    `##` section, so the parser accepts both. Returns (start, end) line indexes.
    """
    section_start = None
    section_end = None
    for i, line in enumerate(lines):
        if (line.startswith("## ") or line.startswith("### ")) and heading_substring in line:
            section_start = i
        elif section_start is not None and (line.startswith("## ") or line.startswith("### ")):
            section_end = i
            break
    return section_start, section_end


def test_agents_md_critical_rules_require_precommit(agents_md: str) -> None:
    """AGENTS.md shared sections must include pre-commit and testing-command guidance.

    Pre-commit hook failures caused ~15 friction events across 15 sessions.
    Elevating it to a Critical Rule prevents repeat loops (FRICT-3A-1).
    The pre-commit rule and the testing-command guidance (task test-all,
    task test-check, task test-filtered) are shared (backend-neutral), so they
    live in AGENTS.md, not physical CLAUDE.md — CLAUDE.md inherits them through
    the `@AGENTS.md` include. This test scans both `Code and Implementation`
    and `Testing Guidelines` sections so that weakening either shared
    obligation is caught.
    """
    lines = agents_md.splitlines()
    code_start, code_end = _section_bounds(lines, "Code and Implementation")
    assert code_start is not None, (
        "AGENTS.md must contain a Code and Implementation section/heading (FRICT-3A-1)"
    )
    test_start, test_end = _section_bounds(lines, "Testing Guidelines")
    assert test_start is not None, (
        "AGENTS.md must contain a Testing Guidelines section/heading for shared "
        "testing-command guidance (task test-all / test-check / test-filtered)."
    )

    def _slice(start: int, end: int | None) -> list[str]:
        return lines[start:end] if end is not None else lines[start:]

    combined_section_text = "\n".join(_slice(code_start, code_end) + _slice(test_start, test_end))
    assert "pre-commit run --all-files" in combined_section_text, (
        "AGENTS.md shared critical-rule and testing guidance sections must include "
        "'pre-commit run --all-files' — the pre-commit Critical Rule lives in "
        "AGENTS.md, not physical CLAUDE.md (FRICT-3A-1)."
    )
    for marker in ("task test-all", "task test-check", "task test-filtered"):
        assert marker in combined_section_text, (
            f"AGENTS.md shared critical-rule and testing guidance sections must "
            f"include '{marker}' — testing-command guidance is shared "
            f"(backend-neutral) and lives in AGENTS.md, not physical CLAUDE.md."
        )


def test_agents_md_session_diagnostics_has_dedicated_heading(agents_md: str) -> None:
    """Session diagnostics must have a dedicated ## section heading, not a trailing paragraph.

    A trailing paragraph after the architecture tree is easy to miss. A named
    section (## **7. Session Diagnostics**) is findable by search and TOC
    navigation (FRICT-7-1).
    """
    lines = agents_md.splitlines()
    heading_lines = [
        line
        for line in lines
        if line.startswith("## ")
        and ("session" in line.lower() or "diagnostics" in line.lower() or "log" in line.lower())
    ]
    assert heading_lines, (
        "AGENTS.md must have a dedicated ## section heading for session diagnostics "
        "(e.g., '## **7. Session Diagnostics**'). Currently it is only a trailing "
        "paragraph after the architecture tree, making it hard to find (FRICT-7-1)."
    )


def test_agents_md_session_diagnostics_mentions_hyphen_convention(agents_md: str) -> None:
    """Session diagnostics section must clarify that path components use hyphens not underscores.

    Session 'f9170655' failed due to underscore vs hyphen mismatch when constructing
    log paths. The convention must be documented explicitly (FRICT-5-2).
    """
    assert "hyphen" in agents_md.lower(), (
        "AGENTS.md session diagnostics must clarify that path components (log "
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


def test_agents_md_has_github_api_discipline(agents_md: str) -> None:
    """AGENTS.md must include GitHub API Call Discipline rule."""
    assert "GitHub API Call Discipline" in agents_md
    assert "sleep 1" in agents_md or "asyncio.sleep(1)" in agents_md
