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


# ── Diminishing-return detection ──────────────────────────────────────────────


def test_diminishing_return_detection_present():
    """SKILL.md must describe diminishing-return detection for finding themes."""
    lower = SKILL_TEXT.lower()
    assert "diminishing" in lower or "goalposts" in lower or "theme comparison" in lower, (
        "resolve-design-review must detect diminishing returns — repeated findings "
        "that are higher-abstraction restatements of previously addressed concerns."
    )


def test_goalposts_reclassified_as_structural():
    """Goalposts-moving findings must be reclassified as STRUCTURAL."""
    lower = SKILL_TEXT.lower()
    has_goalposts_structural = "goalposts" in lower and "structural" in lower
    has_diminishing_structural = "diminishing" in lower and "structural" in lower
    has_reclassify = "reclassif" in lower
    assert has_goalposts_structural or has_diminishing_structural or has_reclassify, (
        "Goalposts-moving findings must be reclassified as STRUCTURAL — "
        "the fix-and-review cycle is not converging on that concern."
    )


def test_revision_guidance_context_input():
    """SKILL.md must accept prior revision_guidance as context for theme comparison."""
    lower = SKILL_TEXT.lower()
    assert "prior" in lower or "previous" in lower, (
        "resolve-design-review must reference prior revision context "
        "for diminishing-return detection."
    )
    assert "[prior_revision_guidance_path]" in SKILL_TEXT or "optional" in lower, (
        "Prior revision_guidance must be an optional argument for backward compatibility."
    )
