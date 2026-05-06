"""SKILL.md compliance tests: structural invariants for skill composition safety.

Validates two classes of end_turn vulnerabilities:

1. **Text-then-tool (intra-step):** A numbered sub-step instructs prose text output
   immediately before the next sub-step instructs a tool call.

2. **Loop-boundary (inter-iteration):** A "For each" loop contains tool invocations
   but lacks an anti-prose guard, allowing the model to emit progress text between
   iterations and create stochastic end_turn windows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]

# Patterns that detect instructions to output/emit/print plain text
_TEXT_OUTPUT_PATTERNS = [
    re.compile(r"output\b.*\b(?:as\s+)?(?:plain\s+)?text", re.IGNORECASE),
    re.compile(r"emit\b.*\btext", re.IGNORECASE),
    re.compile(r"print\b.*\bblock", re.IGNORECASE),
    re.compile(r"output\b.*\bblock\b.*\bplain\s+text", re.IGNORECASE),
]

# Patterns that detect instructions to invoke a tool
_TOOL_CALL_PATTERNS = [
    re.compile(r"(?:load|call|invoke|use)\b.*\bskill\s+tool\b", re.IGNORECASE),
    re.compile(r"THEN\s+load\b.*\bskill", re.IGNORECASE),
]

# Patterns that detect iterative loop constructs in skill prose
_LOOP_HEADER_PATTERNS = [
    re.compile(r"(?i)for\s+each\b"),
    re.compile(r"(?i)repeat\s+for\s+each\b"),
    re.compile(r"(?i)for\s+every\b"),
    re.compile(r"(?i)loop\s+through\b"),
    re.compile(r"(?i)iterate\s+over\b"),
]

# Patterns that detect tool invocations inside loop bodies (superset of _TOOL_CALL_PATTERNS)
_LOOP_TOOL_PATTERNS = [
    *_TOOL_CALL_PATTERNS,
    re.compile(r"(?i)\bwrite\s+tool\b"),
    re.compile(r"(?i)load_recipe\b"),
    re.compile(r"(?i)run_skill\b"),
    re.compile(r"(?i)fetch_github_issue\b"),
    re.compile(r"(?i)merge_worktree\b"),
    re.compile(r"(?i)run_cmd\b"),
    re.compile(r"(?i)run_python\b"),
]

# Patterns that detect anti-prose guard instructions in loop prologues
_ANTI_PROSE_GUARD_PATTERNS = [
    re.compile(r"(?i)do\s+not\s+output\s+(?:any\s+)?prose"),
    re.compile(r"(?i)immediately\s+(?:begin|proceed|start)\b.*\bnext"),
    re.compile(r"(?i)no\s+(?:prose|text|status)\s+(?:between|output)"),
    re.compile(r"(?i)do\s+not\s+emit\s+(?:any\s+)?(?:prose|text|status)"),
]

# Skills whose narration suppression is handled globally by _inject_narration_suppression()
# in build_skill_session_cmd() (headless path) and sous-chef/SKILL.md (cook path).
# Per-loop inline anti-prose guards are intentionally absent — they are redundant.
_GLOBALLY_GUARDED_SKILLS: frozenset[str] = frozenset(
    {
        "process-issues",
        "open-integration-pr",
        "setup-project",
        "collapse-issues",
        "validate-audit",
        "validate-test-audit",
    }
)


def _all_skill_dirs() -> list[Path]:
    """Discover all skill directories that contain a SKILL.md from both skill directories."""
    dirs = []
    for skills_dir in _SKILLS_DIRS:
        dirs.extend(d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists())
    return sorted(dirs, key=lambda d: d.name)


def _skill_text(skill_name: str) -> str:
    result = DefaultSkillResolver().resolve(skill_name)
    assert result is not None, f"Skill not found: {skill_name}"
    return result.path.read_text()


def _has_text_output_instruction(text: str) -> bool:
    """Check if text contains instructions to output prose as plain text."""
    return any(p.search(text) for p in _TEXT_OUTPUT_PATTERNS)


def _has_tool_call_instruction(text: str) -> bool:
    """Check if text contains instructions to make a tool call."""
    return any(p.search(text) for p in _TOOL_CALL_PATTERNS)


def _extract_numbered_substeps(step_text: str) -> list[str]:
    """Split a step into its numbered sub-steps (e.g., **1.**, **2.**, or 1., 2.)."""
    # Match bold-numbered (**1.**) or plain-numbered (1.) sub-step headers
    parts = re.split(r"(?m)^\s*(?:\*\*)?(\d+)\.\s*", step_text)
    # parts[0] is before first numbered item; pairs of (number, content) follow
    substeps = []
    for i in range(1, len(parts), 2):
        if i + 1 < len(parts):
            substeps.append(parts[i + 1])
    return substeps


def _check_text_then_tool(skill_text: str) -> list[str]:
    """Check for text-then-tool anti-pattern in a SKILL.md.

    Returns a list of violation descriptions (empty if compliant).
    Looks for numbered sub-steps where a text output instruction
    immediately precedes a tool call instruction.
    """
    violations: list[str] = []

    # Split into major steps (### Step N or numbered top-level steps)
    step_blocks = re.split(r"(?m)^#{1,3}\s+Step\s+\d+", skill_text)

    for block_idx, block in enumerate(step_blocks):
        substeps = _extract_numbered_substeps(block)
        for i in range(len(substeps) - 1):
            if _has_text_output_instruction(substeps[i]) and _has_tool_call_instruction(
                substeps[i + 1]
            ):
                violations.append(
                    f"Step block {block_idx}: sub-step {i + 1} instructs text output "
                    f"immediately before sub-step {i + 2} which instructs a tool call"
                )
    return violations


def _check_loop_boundary(skill_text: str) -> list[str]:
    """Check for unguarded loop constructs containing tool invocations.

    Returns a list of violation descriptions (empty if compliant).
    Detects 'For each X' loops that contain tool invocations but lack
    an anti-prose guard instruction in the loop prologue.
    """
    violations: list[str] = []
    step_blocks = re.split(r"(?m)^#{1,3}\s+Step\s+\d+", skill_text)

    # Skip block 0 (preamble/description before first Step header) — it contains
    # descriptive text with "for each" phrases that are not executable instructions.
    for block_idx, block in enumerate(step_blocks):
        if block_idx == 0:
            continue
        lines = block.split("\n")
        for line_idx, line in enumerate(lines):
            if not any(p.search(line) for p in _LOOP_HEADER_PATTERNS):
                continue

            # Extract loop body: from this line to next step header or end
            loop_body = "\n".join(lines[line_idx:])

            # Check if loop body contains tool invocations
            has_tool = any(p.search(loop_body) for p in _LOOP_TOOL_PATTERNS)
            if not has_tool:
                continue

            # Extract loop prologue: from loop header to first numbered sub-step
            prologue_match = re.search(r"(?m)^\s*(?:\*\*)?(?:1)\.\s*", loop_body)
            if prologue_match:
                prologue = loop_body[: prologue_match.start()]
            else:
                prologue = loop_body

            # Check for anti-prose guard in prologue or full loop body
            search_text = prologue + "\n" + loop_body
            has_guard = any(p.search(search_text) for p in _ANTI_PROSE_GUARD_PATTERNS)

            if not has_guard:
                loop_preview = line.strip()[:80]
                violations.append(
                    f"Step block {block_idx}: loop '{loop_preview}' contains "
                    f"tool invocations but has no anti-prose guard instruction"
                )

    return violations


@pytest.mark.parametrize("skill_name", ["open-integration-pr"])
def test_no_prose_output_immediately_before_skill_invocation(skill_name: str) -> None:
    """Assert that no SKILL.md step instructs the model to output plain text
    immediately before a Skill tool call.

    The anti-pattern: a step that says "output X as text" followed by
    "then call Skill tool". This creates an end_turn window between
    the text output and the tool call.

    Immune pattern: context is passed via Write tool to a file,
    then the Skill tool is called. Tool-then-tool has no end_turn window.
    """
    text = _skill_text(skill_name)
    violations = _check_text_then_tool(text)
    assert not violations, (
        f"{skill_name}/SKILL.md contains text-then-tool anti-pattern:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


@pytest.mark.parametrize("skill_name", ["open-integration-pr"])
def test_arch_lens_context_via_file_not_prose(skill_name: str) -> None:
    """Assert that PR context for arch-lens skills is passed via a temp
    file (Write tool), not as inline prose text output.

    The SKILL.md must reference writing context to a skill-scoped file path
    (e.g., temp/{skill_name}/pr_arch_lens_context_...) rather than outputting
    it as a conversational text block.
    """
    text = _skill_text(skill_name)
    assert f"{{{{AUTOSKILLIT_TEMP}}}}/{skill_name}/pr_arch_lens_context_" in text, (
        f"{skill_name}/SKILL.md does not reference a skill-scoped pr_arch_lens_context file. "
        "PR context must be written to a skill-scoped temp file, not a shared path."
    )
    assert "Output the PR context block as plain text" not in text, (
        f"{skill_name}/SKILL.md still contains the old prose output instruction."
    )


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda d: d.name)
def test_no_text_then_tool_in_any_step(skill_dir: Path) -> None:
    """No SKILL.md in the project should contain a step that instructs
    the model to output prose text and then make a tool call in the
    same step or consecutive sub-steps, or an unguarded loop with
    tool invocations.

    Skills in _GLOBALLY_GUARDED_SKILLS are exempt from the loop-boundary
    check — their narration suppression is injected at the prompt level
    by build_skill_session_cmd() and sous-chef/SKILL.md.

    This is a project-wide structural invariant, not specific to
    open-pr or arch-lens.
    """
    text = (skill_dir / "SKILL.md").read_text()
    violations = _check_text_then_tool(text)
    if skill_dir.name not in _GLOBALLY_GUARDED_SKILLS:
        violations.extend(_check_loop_boundary(text))
    assert not violations, (
        f"{skill_dir.name}/SKILL.md contains text-then-tool anti-pattern:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# --- Fixture-based test for detecting the old anti-pattern ---


def test_detector_catches_old_pattern() -> None:
    """Verify _check_text_then_tool detects the known vulnerable pattern."""
    old_pattern = """\
### Step 5: Generate Diagrams

**1. Output the PR context block as plain text (NOT as a tool call):**

> Context block here

**2. THEN load the arch-lens skill via the Skill tool** (e.g., `/arch-lens-module-dependency`).
"""
    violations = _check_text_then_tool(old_pattern)
    assert len(violations) >= 1, "Detector failed to catch the text-then-tool anti-pattern"


def test_detector_passes_immune_pattern() -> None:
    """Verify _check_text_then_tool passes the context-file protocol pattern."""
    immune_pattern = """\
### Step 5: Generate Diagrams

**1. Write the PR context to a file using the Write tool:**

- Path: .autoskillit/temp/pr-arch-lens-context.md

**2. Immediately call the Skill tool to load the arch-lens skill.**
"""
    violations = _check_text_then_tool(immune_pattern)
    assert not violations, f"Detector falsely flagged immune pattern: {violations}"


# --- Fixture-based tests for loop-boundary detection ---


def test_detector_catches_unguarded_loop_with_tool() -> None:
    """Verify _check_loop_boundary detects a 'For each' loop containing a tool
    invocation without an anti-prose guard instruction."""
    vulnerable_pattern = """\
### Step 5: Generate Diagrams

For each selected lens, follow this exact sequence:

**1. Write the PR context to a file using the Write tool:**

- Path: .autoskillit/temp/pr-arch-lens-context.md

**2. Immediately call the Skill tool to load the arch-lens skill.**

**3. Follow the loaded skill's instructions.**
"""
    violations = _check_loop_boundary(vulnerable_pattern)
    assert len(violations) >= 1, "Detector failed to catch unguarded loop boundary"


def test_detector_passes_guarded_loop_with_tool() -> None:
    """Verify _check_loop_boundary passes a 'For each' loop that contains
    an anti-prose guard instruction."""
    guarded_pattern = """\
### Step 5: Generate Diagrams

For each selected lens, follow this exact sequence:

**CRITICAL:** Do NOT output any prose status text between lens iterations.
After completing one lens's sub-steps, immediately begin sub-step 1 for the
next lens.

**1. Write the PR context to a file using the Write tool:**

- Path: .autoskillit/temp/pr-arch-lens-context.md

**2. Immediately call the Skill tool to load the arch-lens skill.**

**3. Follow the loaded skill's instructions.**
"""
    violations = _check_loop_boundary(guarded_pattern)
    assert not violations, f"Detector falsely flagged guarded loop: {violations}"


def test_detector_catches_unguarded_mcp_loop() -> None:
    """Verify _check_loop_boundary detects a 'For each' loop containing
    MCP tool invocations (load_recipe, run_skill, fetch_github_issue)
    without an anti-prose guard."""
    vulnerable_pattern = """\
### Step 3: Process Batches

For each issue in the batch (process sequentially):

1. **Fetch issue content:**
   fetch_github_issue(issue_url)

2. **Load the recipe:**
   load_recipe("{recipe_name}")

3. **Execute the recipe.**
"""
    violations = _check_loop_boundary(vulnerable_pattern)
    assert len(violations) >= 1, "Detector failed to catch unguarded MCP loop"


# Detects skills that instruct Agent/Task subagent spawning.
# Any such skill MUST contain the run_in_background prohibition.
_SPAWN_INDICATOR_RE = re.compile(
    r"Task tool|Explore subagent"
    r"|spawn.*subagent|subagent.*spawn|launch.*subagent"
    r"|parallel.*subagent|subagent.*parallel",
    re.IGNORECASE,
)
_BACKGROUND_PROHIBITION_RE = re.compile(r"run_in_background.*prohibited", re.IGNORECASE)

# Skills whose SKILL.md mentions subagents only in a negative/prohibitive context
# (e.g., "rather than spawning subagents", "do not spawn subagents"). The spawn
# indicator regex matches these descriptively — they are not spawning skills.
_NON_SPAWNING_SKILL_DIRS: frozenset[str] = frozenset(
    {
        "report-bug",  # "rather than spawning parallel subagents" — describes non-spawning
        "issue-splitter",  # "do not spawn subagents" — prohibits spawning inline
    }
)


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_no_background_subagent_in_spawning_skills(skill_dir: Path) -> None:
    if skill_dir.name in _NON_SPAWNING_SKILL_DIRS:
        return  # Skill mentions subagents only descriptively/negatively — rule does not apply.
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return
    content = skill_md.read_text(encoding="utf-8")
    if not _SPAWN_INDICATOR_RE.search(content):
        return  # Skill does not spawn subagents — rule does not apply.
    assert _BACKGROUND_PROHIBITION_RE.search(content), (
        f"{skill_dir.name}/SKILL.md contains subagent-spawning instructions "
        "but lacks the background-execution prohibition. "
        "Add to its NEVER block: "
        "'- Run subagents in the background (`run_in_background: true` is prohibited)'"
    )
