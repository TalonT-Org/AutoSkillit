"""SKILL.md compliance tests: structural invariants for skill composition safety.

Validates that no skill instructs the model to output prose text immediately
before a tool call in the same step — the "text-then-tool" anti-pattern that
creates stochastic end_turn windows between text output and tool invocation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

SKILLS_DIR = pkg_root() / "skills"

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


def _all_skill_dirs() -> list[Path]:
    """Discover all skill directories that contain a SKILL.md."""
    return sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


def _skill_text(skill_name: str) -> str:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    assert path.exists(), f"Skill not found: {path}"
    return path.read_text()


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


@pytest.mark.parametrize("skill_name", ["open-pr", "create-review-pr"])
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


@pytest.mark.parametrize("skill_name", ["open-pr", "create-review-pr"])
def test_arch_lens_context_via_file_not_prose(skill_name: str) -> None:
    """Assert that PR context for arch-lens skills is passed via a temp
    file (Write tool), not as inline prose text output.

    The SKILL.md must reference writing context to a file path
    (e.g., temp/pr-arch-lens-context.md) rather than outputting
    it as a conversational text block.
    """
    text = _skill_text(skill_name)
    assert "temp/pr-arch-lens-context.md" in text, (
        f"{skill_name}/SKILL.md does not reference temp/pr-arch-lens-context.md. "
        "PR context must be written to a file, not output as prose."
    )
    assert "Output the PR context block as plain text" not in text, (
        f"{skill_name}/SKILL.md still contains the old prose output instruction."
    )


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda d: d.name)
def test_no_text_then_tool_in_any_step(skill_dir: Path) -> None:
    """No SKILL.md in the project should contain a step that instructs
    the model to output prose text and then make a tool call in the
    same step or consecutive sub-steps.

    This is a project-wide structural invariant, not specific to
    open-pr or arch-lens.
    """
    text = (skill_dir / "SKILL.md").read_text()
    violations = _check_text_then_tool(text)
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

- Path: temp/pr-arch-lens-context.md

**2. Immediately call the Skill tool to load the arch-lens skill.**
"""
    violations = _check_text_then_tool(immune_pattern)
    assert not violations, f"Detector falsely flagged immune pattern: {violations}"
