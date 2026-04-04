"""Contract tests for review-design SKILL.md behavioral encoding."""

import re
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
        assert f"weight={tier}" in skill_text or f"| {tier} " in skill_text, (
            f"Weight tier {tier!r} not found in weight matrix table or explicit weight= notation"
        )


def test_silent_tier_produces_no_output_contract(skill_text):
    """SILENT (S) dimensions must be explicitly contracted to produce no output."""
    assert "SILENT" in skill_text, "SILENT tier label not found in SKILL.md"
    assert "not spawned" in skill_text.lower(), (
        "Behavioral contract 'not spawned' not found — SILENT dimensions must be "
        "explicitly documented as non-spawning"
    )


def test_universal_dimensions_always_run(skill_text):
    """estimand_clarity and hypothesis_falsifiability must be listed as always-run L1."""
    assert "estimand_clarity" in skill_text
    assert "hypothesis_falsifiability" in skill_text
    # Both must be documented as always H-weight (not gated by triage like L4 dimensions)
    assert "always H-weight" in skill_text, (
        "L1 dimensions must be explicitly documented as always H-weight regardless of triage"
    )


# ── Fail-fast gate ──────────────────────────────────────────────────────────


def test_l1_fail_fast_gate_present(skill_text):
    """SKILL.md must encode the L1 fail-fast gate: halt on L1 critical."""
    text_lower = skill_text.lower()
    assert "fail-fast" in text_lower or "fail fast" in text_lower, (
        "L1 fail-fast gate not found in SKILL.md"
    )
    # Must assert that L2+ do NOT run when L1 is critical — use text_lower only
    assert "do not proceed" in text_lower or "halt" in text_lower, (
        "Fail-fast gate must document that L2+ analysis does not run on L1 critical findings"
    )


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
    """Verdict logic must produce GO, REVISE, and STOP outcomes as explicit assignments."""
    for verdict in ["GO", "REVISE", "STOP"]:
        assert f'verdict = "{verdict}"' in skill_text, (
            f"Verdict assignment 'verdict = \"{verdict}\"' not found in verdict logic code block"
        )


def test_verdict_stop_on_l1_critical(skill_text):
    """STOP must be causally linked to L1 dimensions via the stop_triggers code block."""
    # The stop_triggers list must explicitly name the L1 dimensions as triggers
    assert "stop_triggers" in skill_text, "stop_triggers code block not found"
    # Verify causal linkage: stop_triggers assignment must reference both L1 dimensions
    stop_trigger_line = 'f.dimension in {"estimand_clarity", "hypothesis_falsifiability"}'
    assert stop_trigger_line in skill_text, (
        "stop_triggers must explicitly name estimand_clarity and hypothesis_falsifiability "
        "as the L1 STOP-triggering dimensions"
    )


def test_verdict_revise_threshold_defined(skill_text):
    """REVISE threshold (≥3 warnings or any non-L1 critical) must be present."""
    assert "REVISE" in skill_text
    # Assert the precise threshold expression, not just the digit 3
    assert ">= 3" in skill_text or "len(warning_findings) >= 3" in skill_text, (
        "REVISE threshold must be encoded as '>= 3' or 'len(warning_findings) >= 3'"
    )


# ── Dashboard requirements ───────────────────────────────────────────────────


def test_dashboard_cannot_assess_section(skill_text):
    """evaluation_dashboard must include a 'Cannot Assess' section with ≥2 items."""
    # Assert the coupled phrase that ties the section to its minimum count requirement
    assert "Cannot Assess" in skill_text
    assert "Cannot Assess** section with at least 2" in skill_text, (
        "Cannot Assess section must be documented with its minimum count of 2 items"
    )


def test_dashboard_yaml_summary_block(skill_text):
    """evaluation_dashboard must include a machine-readable YAML summary block."""
    assert "# --- review-design machine summary ---" in skill_text, (
        "Machine-readable YAML summary block header not found in SKILL.md"
    )


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


# ── Section-scoped helpers ────────────────────────────────────────────────────


def skill_text_between(start_heading: str, end_heading: str, text: str) -> str:
    """Extract SKILL.md text between two headings (start inclusive, end exclusive)."""
    pattern = re.escape(start_heading) + r".*?(?=" + re.escape(end_heading) + r")"
    m = re.search(pattern, text, re.DOTALL)
    assert m, f"Could not find section '{start_heading}' before '{end_heading}' in SKILL.md"
    return m.group(0)


# ── L1 subagent context and severity calibration ──────────────────────────────


def test_l1_subagents_receive_experiment_type(skill_text: str) -> None:
    """L1 subagents must list experiment_type as an explicit input.

    This is the structural contract that makes the false-STOP regression
    immediately visible: any removal of experiment_type from Step 2 fails here.
    The bug existed because no such assertion was present in the original suite.
    """
    step2_text = skill_text_between("### Step 2", "### Step 3", skill_text)
    assert "experiment_type" in step2_text, (
        "Step 2 L1 subagents must explicitly receive experiment_type as input. "
        "Without it, severity thresholds default to causal_inference standards, "
        "causing false STOP verdicts on benchmark and exploratory plans."
    )


def test_l1_severity_calibration_rubric_present(skill_text: str) -> None:
    """Step 2 must contain a severity calibration rubric for L1 dimensions.

    The rubric is the mechanism that prevents false STOP verdicts: it tells
    the L1 subagent what severity is appropriate per experiment type.
    """
    step2_text = skill_text_between("### Step 2", "### Step 3", skill_text)
    # Rubric must cover the anchoring experiment types
    assert "causal_inference" in step2_text, (
        "Step 2 calibration rubric must specify causal_inference severity thresholds."
    )
    assert "benchmark" in step2_text, (
        "Step 2 calibration rubric must specify benchmark severity thresholds."
    )
    assert "exploratory" in step2_text, (
        "Step 2 calibration rubric must specify exploratory severity thresholds."
    )
    # The rubric must distinguish critical from warning at minimum
    step2_lower = step2_text.lower()
    assert "critical" in step2_lower and "warning" in step2_lower, (
        "Step 2 must define what constitutes critical vs warning per experiment type."
    )


# ── All subagent steps must declare explicit inputs (Part B immunity) ─────────


@pytest.mark.parametrize(
    "step_heading,next_heading",
    [
        ("### Step 2", "### Step 3"),
        ("### Step 3", "### Step 4"),
        ("### Step 4", "### Step 5"),
        ("### Step 5", "### Step 6"),
    ],
)
def test_subagent_steps_declare_explicit_inputs(
    step_heading: str, next_heading: str, skill_text: str
) -> None:
    """Every subagent-spawning step must explicitly list its input variables.

    The false-STOP bug at Step 2 existed because there was no assertion
    requiring explicit input declarations. This parameterized test enforces
    the pattern for all subagent steps, making omission immediately visible.

    The established pattern (from Step 3 red-team) is:
      'Receives: <context variables>'
    or an explicit 'Inputs:' / 'Each ... receives:' block.
    """
    step_text = skill_text_between(step_heading, next_heading, skill_text)
    # Must contain an explicit input declaration using the established pattern
    assert any(
        phrase in step_text.lower()
        for phrase in ["receives:", "inputs:", "each subagent receives", "each ... receives"]
    ), (
        f"{step_heading}: subagent-spawning steps must declare explicit inputs. "
        "Use 'Receives:', 'Inputs:', or 'Each subagent receives:' to list context variables."
    )


@pytest.mark.parametrize(
    "step_heading,next_heading",
    [
        ("### Step 4", "### Step 5"),
        ("### Step 5", "### Step 6"),
    ],
)
def test_l3_l4_subagents_receive_experiment_type(
    step_heading: str, next_heading: str, skill_text: str
) -> None:
    """L3 and L4 subagents must receive experiment_type to calibrate severity.

    While L3/L4 findings route to REVISE (not STOP), type-agnostic severity
    calibration is a structural gap. This test enforces experiment_type
    propagation to all subagent steps.
    """
    step_text = skill_text_between(step_heading, next_heading, skill_text)
    assert "experiment_type" in step_text, (
        f"{step_heading}: L3/L4 subagents must receive experiment_type. "
        "Type-agnostic severity calibration is a structural gap."
    )
