"""Structural guards for audit-impl diff discipline.

Validates:
- Step 2 git diff/log commands use {implementation_ref}, not HEAD
- Step 3 subagent instructions prohibit filesystem reads (Read/Grep/Glob)
- Step 2 declares a diff size guard with chunking strategy
- Parser unit tests for the new extraction functions
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_git_commands,
    extract_step_sections,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILL_MD = _REPO_ROOT / "src" / "autoskillit" / "skills_extended" / "audit-impl" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_content() -> str:
    return _SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def step_sections(skill_content: str) -> dict[str, str]:
    return extract_step_sections(skill_content)


def test_extract_git_commands_captures_inline_backticks() -> None:
    sample = """
Some prose with `git diff base...HEAD --stat` inline.

```bash
git log base..HEAD --oneline
```
"""
    commands = extract_git_commands(sample)
    inline = [c for c in commands if "stat" in c]
    assert inline, "extract_git_commands must capture inline backtick git commands"
    fenced = [c for c in commands if "oneline" in c]
    assert fenced, "extract_git_commands must also capture commands from bash fenced blocks"


def test_extract_step_sections_captures_decimal_steps(skill_content: str) -> None:
    sections = extract_step_sections(skill_content)
    assert "Step 2.5" in sections, (
        "extract_step_sections must produce an entry for 'Step 2.5'; "
        "decimal sub-steps must not be subsumed into their parent integer step"
    )


def test_step2_diff_commands_use_implementation_ref_not_head(
    step_sections: dict[str, str],
) -> None:
    """Step 2 git diff/log commands must reference {implementation_ref}, not HEAD."""
    assert "Step 2" in step_sections, "SKILL.md must contain a Step 2 section"
    step2_text = step_sections["Step 2"]
    git_cmds = extract_git_commands(step2_text)
    diff_log_cmds = [c for c in git_cmds if c.startswith(("git diff", "git log"))]
    assert diff_log_cmds, "Step 2 must contain at least one git diff or git log command"

    head_violations = [
        c
        for c in diff_log_cmds
        # allow 'git rev-parse --abbrev-ref HEAD' — that is a different operation
        if "HEAD" in c and "rev-parse" not in c and "abbrev-ref" not in c
    ]
    assert not head_violations, (
        "Step 2 git diff/log commands must not hardcode HEAD; "
        "use {implementation_ref} or {branch_name} instead. "
        f"Offending commands: {head_violations}"
    )

    ref_present = any("implementation_ref" in c or "branch_name" in c for c in diff_log_cmds)
    assert ref_present, (
        "Step 2 git diff/log commands must reference {implementation_ref} or "
        "{branch_name}. None found."
    )


def test_step3_prohibits_filesystem_reads(step_sections: dict[str, str]) -> None:
    """Step 3 subagent instructions must prohibit Read/Grep/Glob for implementation checks."""
    assert "Step 3" in step_sections, "SKILL.md must contain a Step 3 section"
    step3_text = step_sections["Step 3"]

    assert re.search(r"\b(?:NOT|not|never|NEVER)\b.{0,40}\bRead\b", step3_text), (
        "Step 3 must explicitly prohibit using the Read tool to verify file content. "
        "Add: 'Do NOT call Read, Grep, or Glob to verify file existence or content'"
    )
    assert "Grep" in step3_text and "Glob" in step3_text, (
        "Step 3 must mention both Grep and Glob in its prohibition"
    )
    assert "git show" in step3_text, (
        "Step 3 must provide 'git show {implementation_ref}:{path}' as the escape hatch "
        "for inspecting file content on the implementation branch"
    )


def test_step2_has_diff_size_guard(step_sections: dict[str, str]) -> None:
    """Step 2 must include a diff size guard with a chunking strategy for large diffs."""
    assert "Step 2" in step_sections, "SKILL.md must contain a Step 2 section"
    step2_text = step_sections["Step 2"]

    has_stat = "--stat" in step2_text
    assert has_stat, "Step 2 must use --stat to check diff size before loading the full diff"

    has_threshold = any(
        kw in step2_text for kw in ("files", "characters", "lines", "50,000", "50000", "20 files")
    )
    assert has_threshold, (
        "Step 2 must declare a numeric threshold (file count or character/line limit) "
        "for triggering chunked processing"
    )

    has_chunking = any(
        kw in step2_text.lower() for kw in ("chunk", "batch", "group", "split", "per-file")
    )
    assert has_chunking, "Step 2 must describe a chunking or batching strategy for large diffs"
