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
from autoskillit.recipe.rules.rules_skill_content import (
    _REVIEWS_POST_RE,
    _extract_subsections,
)
from autoskillit.workspace.skills import DefaultSkillResolver
from tests._helpers import extract_always_block, extract_never_block
from tests.contracts._anti_fab_helpers import FABRICATION_GUARD_RE

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
    re.compile(r"(?i)\bspawn\b.*\bsubagent\b"),
    re.compile(r"(?i)\blaunch\b.*\bsubagent\b"),
    re.compile(r"(?i)\bTask\s+tool\b"),
    re.compile(r"(?i)\bAgent\s+tool\b"),
]

# Patterns that detect anti-prose guard instructions in loop prologues
_ANTI_PROSE_GUARD_PATTERNS = [
    re.compile(r"(?i)do\s+not\s+output\s+(?:any\s+)?prose"),
    re.compile(r"(?i)immediately\s+(?:begin|proceed|start)\b.*\bnext"),
    re.compile(r"(?i)no\s+(?:prose|text|status)\s+(?:between|output)"),
    re.compile(r"(?i)do\s+not\s+emit\s+(?:any\s+)?(?:prose|text|status)"),
    re.compile(r"(?i)single\s+(?:message|batch)"),
    re.compile(r"(?i)do\s+not\s+iterate.*(?:turns?|messages?)"),
]

# Patterns for three-layer parallel dispatch reinforcement detection
_PARALLEL_DISPATCH_NEVER_RE = re.compile(
    r"(?i)sequentially.*single.*message|single.*parallel.*message",
)
_PARALLEL_DISPATCH_ALWAYS_RE = re.compile(
    r"(?i)single\s+message",
)
_PARALLEL_DISPATCH_STEP_RE = re.compile(
    r"(?i)single\s+(?:message|batch)|SINGLE\s+MESSAGE",
)

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
        "validate-review-decisions",
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

            # Extract loop body: from this line to next sub-heading or end.
            # Stop at #### or ### sub-headers so tool mentions in unrelated
            # sections of the same step block aren't attributed to this loop.
            remaining_lines = lines[line_idx:]
            loop_end = len(remaining_lines)
            for k, rl in enumerate(remaining_lines):
                if k > 0 and re.match(r"^#{3,4}\s+", rl):
                    loop_end = k
                    break
            loop_body = "\n".join(remaining_lines[:loop_end])

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

            # Check for anti-prose guard in step preamble (before loop header),
            # loop prologue (header to first numbered sub-step), or full loop body.
            # Guard text often appears above the "for each" line in the same step.
            step_preamble = "\n".join(lines[:line_idx])
            search_text = step_preamble + "\n" + prologue + "\n" + loop_body
            has_guard = any(p.search(search_text) for p in _ANTI_PROSE_GUARD_PATTERNS)

            if not has_guard:
                loop_preview = line.strip()[:80]
                violations.append(
                    f"Step block {block_idx}: loop '{loop_preview}' contains "
                    f"tool invocations but has no anti-prose guard instruction"
                )

    return violations


def _check_parallel_dispatch_reinforcement(skill_text: str) -> list[str]:
    """Check for missing three-layer single-message dispatch reinforcement.

    Returns a list of violation descriptions (empty if compliant).
    For skills that spawn parallel subagents, all three layers must be present:
    1. NEVER block prohibits sequential dispatch
    2. ALWAYS block requires single-message dispatch
    3. Step body containing spawn language includes single-message instruction
    """

    violations: list[str] = []

    never_block = extract_never_block(skill_text)
    always_block = extract_always_block(skill_text)

    if not _PARALLEL_DISPATCH_NEVER_RE.search(never_block):
        violations.append(
            "NEVER block does not prohibit sequential dispatch "
            "(expected: 'sequentially...single...message' or 'single...parallel...message')"
        )

    if not _PARALLEL_DISPATCH_ALWAYS_RE.search(always_block):
        violations.append(
            "ALWAYS block does not require single-message dispatch (expected: 'single message')"
        )

    step_blocks = re.split(r"(?m)^#{1,3}\s+Step\s+\d+", skill_text)
    spawning_steps = [b for b in step_blocks if _SPAWN_INDICATOR_RE.search(b)]
    if spawning_steps and not any(_PARALLEL_DISPATCH_STEP_RE.search(b) for b in spawning_steps):
        violations.append(
            "No step block containing spawn language has a single-message dispatch instruction "
            "(expected: 'single message' or 'single batch' in a step with subagent spawning)"
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


_FABRICATION_GUARD_RE = FABRICATION_GUARD_RE


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda d: d.name)
def test_all_skills_have_anti_fabrication_guard(skill_dir: Path) -> None:
    """Every skill with a NEVER block must include anti-fabrication language."""

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        pytest.skip("no SKILL.md")
    text = skill_md.read_text()
    never_block = extract_never_block(text)
    if not never_block:
        pytest.skip(f"{skill_dir.name}: no NEVER block in SKILL.md")
    assert _FABRICATION_GUARD_RE.search(never_block), (
        f"{skill_dir.name}: NEVER block must include anti-fabrication language"
    )


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


_ANTI_BLAME_RE = re.compile(r"(?i)\bblame\b.*\bpre-existing\b")


@pytest.mark.parametrize(
    "skill_dir",
    [d for d in _all_skill_dirs() if d.name.startswith(("implement-", "retry-"))],
    ids=lambda d: d.name,
)
def test_implement_skills_have_anti_blame_prohibition(skill_dir: Path) -> None:
    """All implement-* and retry-* skills must have the anti-blame
    prohibition in their NEVER block."""

    text = (skill_dir / "SKILL.md").read_text()
    never_block = extract_never_block(text)
    assert never_block, f"{skill_dir.name}: implement-/retry- skill has no NEVER block"
    assert _ANTI_BLAME_RE.search(never_block), (
        f"{skill_dir.name}: NEVER block must include anti-blame prohibition. "
        'Add: \'- Blame pre-commit or lint failures on "pre-existing issues" '
        "\u2014 ALL pre-commit checks must pass on the committed code'"
    )


_READ_BEFORE_EDITING_RE = re.compile(r"Read before editing", re.IGNORECASE)
_SUBAGENT_CAVEAT_RE = re.compile(
    r"(?:subagent|child session).*(?:do not|don't|NOT).*(?:satisfy|count)",
    re.IGNORECASE,
)

# Skills that spawn subagents for analysis/triage but NEVER apply file edits.
# The read-before-editing guard is irrelevant for read-only spawning skills.
_NON_EDITING_SPAWNING_SKILL_DIRS: frozenset[str] = frozenset(
    {
        "resolve-design-review",  # Spawns subagents for triage but NEVER blocks fixes
    }
)


@pytest.mark.parametrize(
    "skill_dir",
    [d for d in _all_skill_dirs() if d.name.startswith(("implement-", "retry-", "resolve-"))],
    ids=lambda d: d.name,
)
def test_implement_skills_have_read_before_editing_with_subagent_caveat(skill_dir: Path) -> None:
    """All implement-*, retry-*, and resolve-* skills that spawn subagents must have
    the Read-before-editing instruction with the subagent isolation caveat."""
    if skill_dir.name in _NON_EDITING_SPAWNING_SKILL_DIRS:
        return  # Read-only spawning skill \u2014 guard not applicable.
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return
    content = skill_md.read_text(encoding="utf-8")
    if not _SPAWN_INDICATOR_RE.search(content):
        return  # Skill does not spawn subagents \u2014 rule does not apply.
    assert _READ_BEFORE_EDITING_RE.search(content), (
        f"{skill_dir.name}/SKILL.md spawns subagents but lacks 'Read before editing' instruction."
    )
    assert _SUBAGENT_CAVEAT_RE.search(content), (
        f"{skill_dir.name}/SKILL.md has 'Read before editing' but lacks the subagent isolation "
        "caveat. Add: 'Reads performed by subagents do NOT satisfy this requirement.'"
    )


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_reviews_post_requires_input_flag(skill_dir: Path) -> None:
    """Any SKILL.md section that POSTs to the GitHub Reviews endpoint must use --input -.

    The --field approach serializes JSON arrays as string literals, causing HTTP 422.
    Catches any future skill that adds a POST /pulls/{N}/reviews endpoint without --input -,
    regardless of which skill or which step.

    To verify this test is effective: temporarily remove '--input -' from a reviews POST
    section in any SKILL.md and confirm this test fails. Then restore it.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        pytest.skip(f"{skill_dir.name} has no SKILL.md")
    content = skill_md.read_text(encoding="utf-8")
    for subsection in _extract_subsections(content):
        if _REVIEWS_POST_RE.search(subsection):
            assert "--input -" in subsection, (
                f"{skill_dir.name}/SKILL.md: a section mentions POST to the GitHub Reviews "
                f"endpoint but does not contain '--input -'. The --field approach serializes "
                f"JSON arrays as string literals, causing HTTP 422. "
                f"Use: jq -n ... | gh api .../reviews --method POST --input -"
            )


# --- Fixture-based tests for parallel dispatch reinforcement detector ---


def test_detector_catches_missing_reinforcement() -> None:
    """Verify _check_parallel_dispatch_reinforcement detects missing layers."""
    missing_reinforcement = """\
---
name: example-skill
---

**NEVER:**
- Fabricate information

**ALWAYS:**
- Do something useful

## Workflow

### Step 1: Launch Subagents

Spawn parallel subagents (Task tool) for each item in the list.
For each item, spawn the corresponding subagent.
"""
    violations = _check_parallel_dispatch_reinforcement(missing_reinforcement)
    assert len(violations) == 3, (
        f"Expected 3 violations (one per layer), got {len(violations)}: {violations}"
    )


def test_detector_passes_full_reinforcement() -> None:
    """Verify _check_parallel_dispatch_reinforcement passes a skill with all three layers."""
    full_reinforcement = """\
---
name: example-skill
---

**NEVER:**
- Fabricate information
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Do something useful
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 1: Launch Subagents (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel.**

Spawn parallel subagents (Task tool) for each item in the list.
"""
    violations = _check_parallel_dispatch_reinforcement(full_reinforcement)
    assert not violations, f"Detector falsely flagged fully reinforced skill: {violations}"


def test_detector_catches_unguarded_subagent_spawn_loop() -> None:
    """Verify _check_loop_boundary catches a 'For each...spawn subagent' loop without guard."""
    vulnerable_pattern = """\
### Step 3: Run Subagents

For each dimension name in the list, spawn the corresponding subagent using the Task tool.
"""
    violations = _check_loop_boundary(vulnerable_pattern)
    assert len(violations) >= 1, (
        "Detector failed to catch unguarded 'For each...spawn...Task tool' loop"
    )


def test_detector_passes_guarded_subagent_spawn_loop() -> None:
    """Verify _check_loop_boundary passes a guarded 'For each...Task tool' loop."""
    guarded_pattern = """\
### Step 3: Run Subagents (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per dimension — so they execute in parallel.
Do not iterate through dimensions across multiple turns.**

For each dimension name in the list, spawn the corresponding subagent using the Task tool.
"""
    violations = _check_loop_boundary(guarded_pattern)
    assert not violations, (
        f"Detector falsely flagged single-message guarded spawn loop: {violations}"
    )


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda d: d.name)
def test_parallel_dispatch_has_single_message_reinforcement(skill_dir: Path) -> None:
    """Skills that spawn parallel subagents must include three-layer
    single-message dispatch reinforcement (NEVER + ALWAYS + step body)."""
    text = (skill_dir / "SKILL.md").read_text()
    if skill_dir.name in _NON_SPAWNING_SKILL_DIRS:
        return
    if not _SPAWN_INDICATOR_RE.search(text):
        return
    violations = _check_parallel_dispatch_reinforcement(text)
    assert not violations, (
        f"{skill_dir.name}/SKILL.md spawns parallel subagents but lacks "
        f"single-message dispatch reinforcement:\n" + "\n".join(f"  - {v}" for v in violations)
    )
