"""Structural guards for resolve-design-review SKILL.md.

Tests enforce the triage flow, ADDRESSABLE/STRUCTURAL/DISCUSS classification,
parallel subagents, conditional guidance emission, and structured output tokens.
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-design-review"
    / "SKILL.md"
)
SKILL_TEXT = SKILL_PATH.read_text()


def test_addressable_structural_discuss_classifications():
    """SKILL.md must define ADDRESSABLE, STRUCTURAL, and DISCUSS classifications."""
    assert "ADDRESSABLE" in SKILL_TEXT
    assert "STRUCTURAL" in SKILL_TEXT
    assert "DISCUSS" in SKILL_TEXT


def test_resolution_revised_when_any_addressable():
    """SKILL.md must state resolution=revised when any finding is ADDRESSABLE."""
    lower = SKILL_TEXT.lower()
    assert "revised" in lower and "addressable" in lower


def test_resolution_failed_when_all_structural():
    """SKILL.md must state resolution=failed when all findings are STRUCTURAL."""
    lower = SKILL_TEXT.lower()
    assert "failed" in lower and "structural" in lower


def test_revision_guidance_conditional_emission():
    """revision_guidance must be emitted ONLY when resolution=revised."""
    assert "revision_guidance" in SKILL_TEXT
    lower = SKILL_TEXT.lower()
    assert (
        "only when" in lower
        or "emitted only" in lower
        or "only emitted" in lower
        or "only when resolution" in lower
    )


def test_parallel_subagents_for_classification():
    """SKILL.md must describe parallel subagents for feasibility validation."""
    lower = SKILL_TEXT.lower()
    assert "parallel" in lower
    assert "subagent" in lower or "sub-agent" in lower or "task tool" in lower


def test_analysis_before_guidance_in_workflow():
    """Analysis phase must be documented before guidance generation."""
    lower = SKILL_TEXT.lower()
    analysis_idx = lower.find("analysis")
    guidance_idx = lower.find("revision guidance")
    assert analysis_idx != -1, "SKILL.md must describe an analysis phase"
    assert guidance_idx != -1, "SKILL.md must describe revision guidance generation"
    assert analysis_idx < guidance_idx, (
        "Analysis must appear before guidance generation in SKILL.md"
    )


def test_exit_zero_always():
    """SKILL.md must state that exit 0 applies to both outcomes."""
    lower = SKILL_TEXT.lower()
    assert "exit 0" in lower


def test_structured_output_tokens_present():
    """SKILL.md must define resolution= structured output token and %%ORDER_UP%%."""
    assert "resolution = " in SKILL_TEXT
    assert "%%ORDER_UP%%" in SKILL_TEXT


def test_temp_directory_is_resolve_design_review():
    """SKILL.md must use .autoskillit/temp/resolve-design-review/ for output."""
    assert ".autoskillit/temp/resolve-design-review/" in SKILL_TEXT
