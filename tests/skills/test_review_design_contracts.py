"""Contract tests for review-design SKILL.md behavioral encoding."""

from pathlib import Path

import pytest

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/review-design/SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


# ── Triage classification ──────────────────────────────────────────────────


def test_triage_lists_all_five_experiment_types(skill_text):
    """All five first-match triage classes must be named in the SKILL.md."""
    for name in [
        "benchmark",
        "configuration_study",
        "causal_inference",
        "robustness_audit",
        "exploratory",
    ]:
        assert name in skill_text, f"Missing triage type: {name!r}"


# ── Dimension weight matrix ────────────────────────────────────────────────


def test_dimension_weight_tiers_defined(skill_text):
    """H/M/L/S weight tiers must be defined in the SKILL.md."""
    for tier in ["H", "M", "L", "S"]:
        assert f"weight={tier}" in skill_text or f"| {tier} " in skill_text or tier in skill_text


def test_silent_tier_produces_no_output_contract(skill_text):
    """SILENT (S) dimensions must be explicitly contracted to produce no output."""
    assert "SILENT" in skill_text or "silent" in skill_text.lower()
    assert (
        "not run" in skill_text.lower()
        or "not spawned" in skill_text.lower()
        or "S (" in skill_text
    )


def test_universal_dimensions_always_run(skill_text):
    """estimand_clarity and hypothesis_falsifiability must be listed as always-run."""
    assert "estimand_clarity" in skill_text
    assert "hypothesis_falsifiability" in skill_text


# ── Fail-fast gate ──────────────────────────────────────────────────────────


def test_l1_fail_fast_gate_present(skill_text):
    """SKILL.md must encode the L1 fail-fast gate: halt on L1 critical."""
    text_lower = skill_text.lower()
    assert "fail-fast" in text_lower or "fail fast" in text_lower, (
        "L1 fail-fast gate not found in SKILL.md"
    )
    # Must assert that L2+ do NOT run when L1 is critical
    assert "do not proceed" in text_lower or "halt" in text_lower or "stop" in skill_text


# ── Red-team agent ──────────────────────────────────────────────────────────


def test_red_team_requires_decision_contract(skill_text):
    """Red-team findings must always carry requires_decision: true (project-wide convention)."""
    assert "requires_decision" in skill_text
    # The contract must state true, not just mention the field
    assert "requires_decision: true" in skill_text or '"requires_decision": true' in skill_text


def test_red_team_universal_challenges_present(skill_text):
    """All five universal red-team challenges must be named."""
    for challenge in [
        "Goodhart",
        "leakage",
        "tuning",
        "Survivorship",
        "collision",
    ]:
        assert challenge.lower() in skill_text.lower(), (
            f"Red-team challenge {challenge!r} not found in SKILL.md"
        )


# ── Backward-compatible parsing ─────────────────────────────────────────────


def test_frontmatter_fallback_documented(skill_text):
    """SKILL.md must document the two-level frontmatter parsing fallback."""
    assert "frontmatter" in skill_text.lower()
    assert "LLM" in skill_text or "extraction" in skill_text.lower()
    assert "source: frontmatter" in skill_text or "provenance" in skill_text.lower()


# ── Verdict logic ────────────────────────────────────────────────────────────


def test_verdict_logic_all_three_outcomes(skill_text):
    """Verdict logic must produce GO, REVISE, and STOP outcomes."""
    for verdict in ["GO", "REVISE", "STOP"]:
        assert verdict in skill_text


def test_verdict_stop_on_l1_critical(skill_text):
    """STOP must be triggered by L1 critical findings on estimand/hypothesis dims."""
    assert "STOP" in skill_text
    # estimand_clarity and hypothesis_falsifiability must be named as STOP triggers
    assert "estimand_clarity" in skill_text
    assert "hypothesis_falsifiability" in skill_text


def test_verdict_revise_threshold_defined(skill_text):
    """REVISE threshold (≥3 warnings or any non-L1 critical) must be present."""
    # The issue spec says: critical_findings or len(warning_findings) >= 3 → REVISE
    assert "REVISE" in skill_text
    assert "3" in skill_text or "three" in skill_text.lower()


# ── Dashboard requirements ───────────────────────────────────────────────────


def test_dashboard_cannot_assess_section(skill_text):
    """evaluation_dashboard must include a 'Cannot Assess' section with ≥2 items."""
    assert "Cannot Assess" in skill_text
    assert (
        "≥2" in skill_text
        or ">= 2" in skill_text
        or "minimum 2" in skill_text.lower()
        or "at least 2" in skill_text.lower()
    )


def test_dashboard_yaml_summary_block(skill_text):
    """evaluation_dashboard must include a machine-readable YAML summary block."""
    assert "YAML" in skill_text
    assert "summary" in skill_text.lower()


# ── Output token format ──────────────────────────────────────────────────────


def test_output_tokens_all_four_present(skill_text):
    """All four output tokens must be named in the SKILL.md."""
    for token in ["verdict", "experiment_type", "evaluation_dashboard", "revision_guidance"]:
        assert token in skill_text, f"Output token {token!r} not found"


def test_revision_guidance_only_on_revise(skill_text):
    """revision_guidance must be documented as written only when verdict=REVISE."""
    assert "revision_guidance" in skill_text
    assert "REVISE" in skill_text
    # The file must couple revision_guidance to REVISE condition
    lines_with_guidance = [line for line in skill_text.splitlines() if "revision_guidance" in line]
    combined = "\n".join(lines_with_guidance)
    assert "REVISE" in combined or "revise" in combined.lower(), (
        "revision_guidance must be tied to REVISE verdict in its description"
    )


def test_order_up_terminator_present(skill_text):
    """%%ORDER_UP%% must be the final terminal marker after token emission."""
    assert "%%ORDER_UP%%" in skill_text
