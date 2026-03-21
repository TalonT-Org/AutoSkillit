"""Contract tests for the PARALLEL STEP SCHEDULING section in sous-chef SKILL.md."""

from __future__ import annotations

from pathlib import Path

import pytest


def _sous_chef_text() -> str:
    skill_md = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "autoskillit" / "skills" / "sous-chef" / "SKILL.md"
    )
    return skill_md.read_text()


REQUIRED_FAST_STEPS = [
    "run_cmd",
    "clone_repo",
    "create_unique_branch",
    "fetch_github_issue",
    "claim_issue",
    "merge_worktree",
    "test_check",
    "reset_test_dir",
    "classify_fix",
]


def test_sous_chef_has_parallel_scheduling_section() -> None:
    """REQ-PROMPT-001: sous-chef SKILL.md has PARALLEL STEP SCHEDULING section marked MANDATORY."""
    text = _sous_chef_text()
    assert "PARALLEL STEP SCHEDULING" in text, (
        "sous-chef SKILL.md must contain a PARALLEL STEP SCHEDULING section"
    )
    assert "MANDATORY" in text[text.index("PARALLEL STEP SCHEDULING"):text.index("PARALLEL STEP SCHEDULING") + 60], (
        "PARALLEL STEP SCHEDULING section must be marked MANDATORY"
    )


@pytest.mark.parametrize("tool", REQUIRED_FAST_STEPS)
def test_sous_chef_parallel_scheduling_defines_fast_step(tool: str) -> None:
    """REQ-PROMPT-002: Section must list all required fast-step MCP tool names."""
    text = _sous_chef_text()
    section_start = text.find("PARALLEL STEP SCHEDULING")
    assert section_start != -1
    # Find the next major section (## ...) after PARALLEL STEP SCHEDULING
    next_section = text.find("\n## ", section_start + 1)
    section_text = text[section_start:next_section] if next_section != -1 else text[section_start:]
    assert tool in section_text, (
        f"PARALLEL STEP SCHEDULING section must list '{tool}' as a fast step"
    )


def test_sous_chef_parallel_scheduling_defines_slow_steps_as_run_skill() -> None:
    """REQ-PROMPT-003: Section must define slow steps as run_skill invocations."""
    text = _sous_chef_text()
    section_start = text.find("PARALLEL STEP SCHEDULING")
    assert section_start != -1
    next_section = text.find("\n## ", section_start + 1)
    section_text = text[section_start:next_section] if next_section != -1 else text[section_start:]
    assert "run_skill" in section_text, (
        "PARALLEL STEP SCHEDULING section must mention run_skill as slow steps"
    )


def test_sous_chef_parallel_scheduling_prohibits_slow_before_all_fast_done() -> None:
    """REQ-PROMPT-004 + REQ-PROMPT-006: Section prohibits launching slow step while fast steps pending."""
    text = _sous_chef_text()
    section_start = text.find("PARALLEL STEP SCHEDULING")
    assert section_start != -1
    next_section = text.find("\n## ", section_start + 1)
    section_text = text[section_start:next_section] if next_section != -1 else text[section_start:]
    # Check that there is prohibition language about slow + fast steps co-existing
    lower = section_text.lower()
    has_prohibition = (
        "never" in lower or "must not" in lower or "do not" in lower or "prohibited" in lower
    )
    assert has_prohibition, (
        "PARALLEL STEP SCHEDULING section must explicitly prohibit launching slow steps "
        "while another pipeline still has fast steps pending"
    )


def test_sous_chef_parallel_scheduling_batches_slow_steps() -> None:
    """REQ-PROMPT-005: Section must instruct launching all slow steps together in one parallel batch."""
    text = _sous_chef_text()
    section_start = text.find("PARALLEL STEP SCHEDULING")
    assert section_start != -1
    next_section = text.find("\n## ", section_start + 1)
    section_text = text[section_start:next_section] if next_section != -1 else text[section_start:]
    lower = section_text.lower()
    assert "together" in lower or "parallel batch" in lower or "all slow" in lower or "batch" in lower, (
        "PARALLEL STEP SCHEDULING section must instruct launching all slow steps together"
    )


def test_sous_chef_parallel_scheduling_explains_wall_clock_rationale() -> None:
    """REQ-PROMPT-007: Section must explain the wall-clock rationale."""
    text = _sous_chef_text()
    section_start = text.find("PARALLEL STEP SCHEDULING")
    assert section_start != -1
    next_section = text.find("\n## ", section_start + 1)
    section_text = text[section_start:next_section] if next_section != -1 else text[section_start:]
    lower = section_text.lower()
    assert "wall-clock" in lower or "wall clock" in lower or "idle" in lower or "slowest" in lower, (
        "PARALLEL STEP SCHEDULING section must explain wall-clock rationale (idle time, slowest step)"
    )


def test_sous_chef_scheduling_section_placement() -> None:
    """REQ-PROMPT-008: PARALLEL STEP SCHEDULING section must appear after MULTIPLE ISSUES."""
    text = _sous_chef_text()
    multiple_issues_pos = text.find("MULTIPLE ISSUES")
    scheduling_pos = text.find("PARALLEL STEP SCHEDULING")
    assert multiple_issues_pos != -1, "MULTIPLE ISSUES section must exist in sous-chef SKILL.md"
    assert scheduling_pos != -1, "PARALLEL STEP SCHEDULING section must exist"
    assert scheduling_pos > multiple_issues_pos, (
        "PARALLEL STEP SCHEDULING section must appear after MULTIPLE ISSUES section"
    )
