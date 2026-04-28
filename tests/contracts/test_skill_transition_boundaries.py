"""Contract tests for anti-confirmation instructions at SKILL.md transition boundaries."""

from __future__ import annotations

import re
from pathlib import Path

_ANTI_CONFIRM_RE = re.compile(
    r"(?:never|do\s+not|must\s+not).*(?:ask|confirm|pause|AskUserQuestion)",
    re.IGNORECASE,
)


def _skill_text(skill_name: str) -> str:
    base = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit"
    for tier in ("skills", "skills_extended"):
        p = base / tier / skill_name / "SKILL.md"
        if p.exists():
            return p.read_text()
    raise FileNotFoundError(f"SKILL.md not found for skill: {skill_name!r}")


def test_process_issues_batch_anti_confirmation() -> None:
    """Critical Constraints section must prohibit AskUserQuestion at batch transitions."""
    text = _skill_text("process-issues")
    cc_start = text.find("## Critical Constraints")
    assert cc_start != -1, "process-issues must have a Critical Constraints section"
    next_section = text.find("\n## ", cc_start + 1)
    section = text[cc_start:next_section] if next_section != -1 else text[cc_start:]
    assert _ANTI_CONFIRM_RE.search(section) is not None, (
        "process-issues Critical Constraints must explicitly prohibit AskUserQuestion "
        "at batch transitions (e.g., 'NEVER use AskUserQuestion ... batch')"
    )


def test_process_issues_inter_issue_anti_confirmation() -> None:
    """Critical Constraints section must prohibit AskUserQuestion between issues."""
    text = _skill_text("process-issues")
    cc_start = text.find("## Critical Constraints")
    assert cc_start != -1, "process-issues must have a Critical Constraints section"
    next_section = text.find("\n## ", cc_start + 1)
    section = text[cc_start:next_section] if next_section != -1 else text[cc_start:]
    assert _ANTI_CONFIRM_RE.search(section) is not None, (
        "process-issues Critical Constraints must explicitly prohibit AskUserQuestion "
        "between issues (e.g., 'NEVER use AskUserQuestion ... issue')"
    )


def test_implement_worktree_phase_anti_confirmation() -> None:
    """Phase iteration section must prohibit pausing for confirmation between phases."""
    text = _skill_text("implement-worktree")
    phase_pos = text.find("Phase by Phase")
    assert phase_pos != -1, "implement-worktree must have a 'Phase by Phase' section"
    next_section = text.find("\n### ", phase_pos + 1)
    if next_section == -1:
        next_section = text.find("\n## ", phase_pos + 1)
    section = text[phase_pos:next_section] if next_section != -1 else text[phase_pos:]
    assert _ANTI_CONFIRM_RE.search(section) is not None, (
        "implement-worktree phase iteration section must explicitly prohibit "
        "pausing for confirmation between phases"
    )


def test_retry_worktree_phase_anti_confirmation() -> None:
    """Step 3 loop section must prohibit AskUserQuestion between phase iterations."""
    text = _skill_text("retry-worktree")
    step3_pos = text.find("Step 3: Continue Implementation")
    assert step3_pos != -1, "retry-worktree must have a 'Step 3: Continue Implementation' section"
    next_section = text.find("\n### ", step3_pos + 1)
    if next_section == -1:
        next_section = text.find("\n## ", step3_pos + 1)
    section = text[step3_pos:next_section] if next_section != -1 else text[step3_pos:]
    assert _ANTI_CONFIRM_RE.search(section) is not None, (
        "retry-worktree Step 3 loop section must explicitly prohibit "
        "AskUserQuestion between phase iterations"
    )
