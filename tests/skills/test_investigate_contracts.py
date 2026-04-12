"""Structural contracts for the investigate historical recurrence check step.

Validates that investigate/SKILL.md carries the required instructions
for Step 3.5: Historical Recurrence Check — the JSONL log mining, git
history scan, and conditional analysis between Step 3 (Synthesize) and
Step 4 (Write Report).
"""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def skill_text() -> str:
    skill_path = pkg_root() / "skills_extended" / "investigate" / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
    return skill_path.read_text()


@pytest.fixture(scope="module")
def step_35_section(skill_text: str) -> str:
    """Extract only the Step 3.5 section text."""
    step_35_idx = skill_text.find("Step 3.5")
    if step_35_idx == -1:
        pytest.fail("Step 3.5 not found in investigate SKILL.md")
    step_4_idx = skill_text.find("### Step 4", step_35_idx)
    if step_4_idx != -1:
        return skill_text[step_35_idx:step_4_idx]
    return skill_text[step_35_idx:]


@pytest.fixture(scope="module")
def report_section(skill_text: str) -> str:
    """Extract the Step 4: Write Report section text.

    Uses the next top-level ``## `` heading that is NOT inside a fenced
    code block as the boundary. The report template contains ``## ``
    headings inside a markdown code fence (```markdown ... ```) which must
    be included — so we skip ``## `` occurrences between fence markers.
    """
    step_4_idx = skill_text.find("### Step 4:")
    if step_4_idx == -1:
        pytest.fail("Step 4 not found in investigate SKILL.md")
    in_fence = False
    lines = skill_text[step_4_idx:].split("\n")
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## ") and not line.startswith("### "):
            end_idx = step_4_idx + sum(len(prev) + 1 for prev in lines[:i])
            return skill_text[step_4_idx:end_idx]
    return skill_text[step_4_idx:]


def test_investigate_has_historical_recurrence_step(skill_text: str) -> None:
    """investigate must contain a Step 3.5: Historical Recurrence Check section."""
    assert "Step 3.5" in skill_text, (
        "investigate SKILL.md must contain a 'Step 3.5: Historical Recurrence Check' "
        "section inserted between Step 3 (Synthesize Findings) and Step 4 (Write Report)"
    )


def test_investigate_historical_step_positioned_after_step3(skill_text: str) -> None:
    """Step 3.5 must appear after Step 3 and before Step 4."""
    step_3_idx = skill_text.find("### Step 3:")
    step_35_idx = skill_text.find("Step 3.5")
    step_4_idx = skill_text.find("### Step 4:")
    assert step_3_idx != -1, "Step 3 heading ('### Step 3:') not found in investigate SKILL.md"
    assert step_35_idx != -1, "Step 3.5 heading ('Step 3.5') not found in investigate SKILL.md"
    assert step_4_idx != -1, "Step 4 heading ('### Step 4:') not found in investigate SKILL.md"
    assert step_3_idx < step_35_idx < step_4_idx, (
        "Step 3.5 must be positioned after Step 3 and before Step 4 in investigate SKILL.md"
    )


def test_investigate_historical_step_mines_jsonl_logs(step_35_section: str) -> None:
    """Step 3.5 must instruct mining ~/.claude/projects/ JSONL logs."""
    assert ".claude/projects/" in step_35_section, (
        "Step 3.5 must reference '.claude/projects/' as the source directory for "
        "prior Claude Code conversation logs containing /investigate invocations"
    )
    assert ".jsonl" in step_35_section or "JSONL" in step_35_section, (
        "Step 3.5 must reference '.jsonl' or 'JSONL' as the log file format to scan"
    )


def test_investigate_historical_step_searches_prior_invocations(step_35_section: str) -> None:
    """Step 3.5 must search for prior /investigate or skill invocations."""
    has_skill_grep = "investigate" in step_35_section.lower()
    has_grep_pattern = "grep" in step_35_section.lower()
    assert has_skill_grep and has_grep_pattern, (
        "Step 3.5 must instruct grep-based scanning for prior /investigate invocations "
        "in the JSONL logs"
    )


def test_investigate_historical_step_uses_git_log(step_35_section: str) -> None:
    """Step 3.5 must use git log to find prior fix commits."""
    assert "git log" in step_35_section, (
        "Step 3.5 must use 'git log' to scan recent commit history for prior fix commits "
        "that touched the affected components"
    )


def test_investigate_historical_step_checks_fix_keywords(step_35_section: str) -> None:
    """Step 3.5 must scan for fix and revert commit keywords."""
    has_keywords = "fix" in step_35_section.lower() and "revert" in step_35_section.lower()
    assert has_keywords, (
        "Step 3.5 must list 'fix' and 'revert' as commit message keywords to scan for "
        "when detecting prior fixes (expected in git --grep pattern or keyword list)"
    )


def test_investigate_historical_step_scoped_to_recent_history(step_35_section: str) -> None:
    """Step 3.5 must scope git scan to bounded recent history."""
    has_depth_limit = (
        "-100" in step_35_section
        or "--since" in step_35_section
        or "100 commit" in step_35_section.lower()
    )
    assert has_depth_limit, (
        "Step 3.5 must scope the git log scan to a bounded recent history "
        "(e.g., last 100 commits or --since <duration>) — unbounded all-history "
        "scanning is too expensive for a lightweight recurrence check"
    )


def test_investigate_historical_step_conditional_subagent(step_35_section: str) -> None:
    """Step 3.5 must conditionally spawn a subagent only when history is found."""
    has_conditional = "if" in step_35_section.lower() and (
        "found" in step_35_section.lower() or "match" in step_35_section.lower()
    )
    has_subagent = "subagent" in step_35_section.lower() or "Task" in step_35_section
    assert has_conditional and has_subagent, (
        "Step 3.5 must conditionally spawn a subagent only when prior history is found — "
        "first-occurrence bugs must have a zero-overhead fast path"
    )


def test_investigate_historical_step_no_history_fast_path(step_35_section: str) -> None:
    """Step 3.5 must have a fast path when no history is found (no subagent calls)."""
    assert "no prior" in step_35_section.lower() or "no history" in step_35_section.lower(), (
        "Step 3.5 must explicitly describe the no-history fast path ('No prior "
        "investigations...' or equivalent) so the skill records a single-line result "
        "without spawning an analysis subagent"
    )


def test_investigate_report_template_has_historical_context(report_section: str) -> None:
    """Step 4 report template must include a ## Historical Context section."""
    assert "Historical Context" in report_section, (
        "Step 4 report template must include a '## Historical Context' section populated "
        "by the Step 3.5 analysis"
    )


def test_investigate_historical_context_after_similar_patterns(report_section: str) -> None:
    """Historical Context must appear after Similar Patterns in the report template."""
    similar_idx = report_section.find("Similar Patterns")
    historical_idx = report_section.find("Historical Context")
    assert similar_idx != -1, "'Similar Patterns' section not found in Step 4 report template"
    assert historical_idx != -1, "'Historical Context' section not found in Step 4 report template"
    assert similar_idx < historical_idx, (
        "'Historical Context' must be positioned after 'Similar Patterns' in the "
        "report template ordering"
    )


def test_investigate_historical_step_flags_rectify(step_35_section: str) -> None:
    """Step 3.5 must flag /autoskillit:rectify when patterns are recurring."""
    assert "rectify" in step_35_section.lower(), (
        "Step 3.5 must reference '/autoskillit:rectify' as the follow-up remediation "
        "path when prior fixes are insufficient and the root cause is recurring"
    )


def test_investigate_synthesize_includes_historical_context(skill_text: str) -> None:
    """Step 3 synthesis list must include Historical Context as a finding item."""
    step_3_idx = skill_text.find("### Step 3:")
    step_35_idx = skill_text.find("Step 3.5")
    assert step_3_idx != -1 and step_35_idx != -1
    step_3_text = skill_text[step_3_idx:step_35_idx]
    assert "Historical Context" in step_3_text, (
        "Step 3 synthesis numbered list must include 'Historical Context' as a finding "
        "item so the report template sections are aligned with the synthesized findings"
    )


def test_investigate_historical_step_excludes_subagent_dirs(step_35_section: str) -> None:
    """Step 3.5 must exclude subagent log subdirectories from JSONL scanning."""
    assert "subagent" in step_35_section.lower(), (
        "Step 3.5 must explicitly exclude subagent log subdirectories (e.g., "
        "'*/subagents/*') when scanning JSONL logs — otherwise the scan double-counts "
        "results from every prior subagent conversation"
    )


def test_investigate_historical_step_reads_prior_diffs(step_35_section: str) -> None:
    """Step 3.5 analysis subagent must read prior fix diffs via git show."""
    assert "git show" in step_35_section or "git log -p" in step_35_section, (
        "Step 3.5 Part C analysis subagent must read prior fix diffs via 'git show' "
        "or 'git log -p' so it can compare the prior fix approach against the current "
        "root cause"
    )
