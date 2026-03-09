"""Tests that CLAUDE.md contains required critical rules and documentation.

Encodes behavioral contracts derived from friction analysis (issue #250):
- FRICT-1B-3: set_project_path initialization rule in §3.5
- FRICT-3A-1: pre-commit critical rule in §3.1
- FRICT-5-2: session diagnostics hyphen path convention documented
- FRICT-7-1: session diagnostics under a dedicated heading, not trailing paragraph
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_MD = (REPO_ROOT / "CLAUDE.md").read_text()


def test_claude_md_code_index_requires_set_project_path() -> None:
    """§3.5 must instruct agents to call set_project_path before using code-index tools.

    Without this call every code-index tool fails with 'Project path not set',
    cascading into parallel call cancellations (FRICT-1B-3).
    """
    # Find the §3.5 section
    assert "3.5" in CLAUDE_MD, "§3.5 section not found in CLAUDE.md"
    section_start = CLAUDE_MD.index("3.5")
    # Find the next top-level section heading (### 3.) to bound the search
    next_section = CLAUDE_MD.find("### **3.", section_start + 1)
    section_text = (
        CLAUDE_MD[section_start:next_section] if next_section != -1 else CLAUDE_MD[section_start:]
    )
    assert "set_project_path" in section_text, (
        "CLAUDE.md §3.5 (Code Index MCP Usage) must instruct agents to call "
        "set_project_path before using any code-index tool in a new session (FRICT-1B-3)."
    )


def test_claude_md_critical_rules_require_precommit() -> None:
    """§3.1 (Code and Implementation) must include a pre-commit critical rule.

    Pre-commit hook failures caused ~15 friction events across 15 sessions.
    Elevating it to a Critical Rule (not just §5 info) prevents repeat loops
    (FRICT-3A-1).
    """
    # Find the §3.1 section
    assert "3.1" in CLAUDE_MD, "§3.1 section not found in CLAUDE.md"
    section_start = CLAUDE_MD.index("3.1")
    next_section = CLAUDE_MD.find("### **3.", section_start + 1)
    section_text = (
        CLAUDE_MD[section_start:next_section] if next_section != -1 else CLAUDE_MD[section_start:]
    )
    assert "pre-commit run --all-files" in section_text, (
        "CLAUDE.md §3.1 (Code and Implementation) must include a Critical Rule "
        "requiring 'pre-commit run --all-files' before committing (FRICT-3A-1)."
    )


def test_claude_md_session_diagnostics_has_dedicated_heading() -> None:
    """Session diagnostics must have a dedicated ## section heading, not a trailing paragraph.

    A trailing paragraph after the architecture tree is easy to miss. A named
    section (## **7. Session Diagnostics**) is findable by search and TOC
    navigation (FRICT-7-1).
    """
    lines = CLAUDE_MD.splitlines()
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


def test_claude_md_session_diagnostics_mentions_hyphen_convention() -> None:
    """Session diagnostics section must clarify that path components use hyphens not underscores.

    Session 'f9170655' failed due to underscore vs hyphen mismatch when constructing
    log paths. The convention must be documented explicitly (FRICT-5-2).
    """
    assert "hyphen" in CLAUDE_MD.lower(), (
        "CLAUDE.md session diagnostics must clarify that path components (log "
        "directory names, session folder names) use hyphens, not underscores "
        "(FRICT-5-2). Without this, agents construct wrong paths."
    )
