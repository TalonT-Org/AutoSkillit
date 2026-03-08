"""SKILL.md compliance tests: assert that arch-lens invocations use the
context-before-Skill pattern, not the broken post-Skill context injection.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root

SKILLS_DIR = pkg_root() / "skills"

POST_SKILL_INJECTION_PATTERNS = [
    re.compile(r"immediately provide the following context", re.IGNORECASE),
    re.compile(r"provide the following context message", re.IGNORECASE),
    re.compile(r"same pattern as open-pr Step 5", re.IGNORECASE),
    re.compile(r"provide.*context.*after.*skill", re.IGNORECASE),
    re.compile(r"follow-up message in the same.*turn", re.IGNORECASE),
]


def _skill_text(skill_name: str) -> str:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    assert path.exists(), f"Skill not found: {path}"
    return path.read_text()


@pytest.mark.parametrize("skill_name", ["open-pr", "create-review-pr"])
def test_skill_has_no_post_skill_context_injection(skill_name: str) -> None:
    """Skills that invoke arch-lens must NOT tell the model to produce context
    text in the same turn as a Skill tool call return. This pattern causes
    non-deterministic end_turn exits. The context must be output BEFORE the
    Skill tool call.
    """
    text = _skill_text(skill_name)
    violations = [p.pattern for p in POST_SKILL_INJECTION_PATTERNS if p.search(text)]
    assert not violations, (
        f"{skill_name}/SKILL.md contains post-Skill context injection anti-pattern.\n"
        f"Matching patterns: {violations}\n"
        "Context text must appear BEFORE the Skill tool call, not after."
    )


@pytest.mark.parametrize("skill_name", ["open-pr", "create-review-pr"])
def test_skill_context_precedes_arch_lens_call(skill_name: str) -> None:
    """In skills that invoke arch-lens, the PR context block (★/● file markers)
    must appear in the instruction text BEFORE the 'load arch-lens skill via
    Skill tool' instruction, not after it.
    """
    text = _skill_text(skill_name)
    context_marker_idx = text.find("★")
    skill_call_idx = text.find("Skill tool")
    if context_marker_idx == -1 or skill_call_idx == -1:
        return  # skill doesn't use arch-lens, skip
    assert context_marker_idx < skill_call_idx, (
        f"{skill_name}/SKILL.md: PR context markers (★/●) appear AFTER 'Skill tool' "
        f"mention (pos {context_marker_idx} > {skill_call_idx}). "
        "Context must precede the Skill tool call."
    )
