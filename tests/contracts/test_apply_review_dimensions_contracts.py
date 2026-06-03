"""Contract tests for apply-review-dimensions SKILL.md behavioral encoding."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILL_MD = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "apply-review-dimensions"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


def skill_text_between(start_heading: str, end_heading: str, text: str) -> str:
    """Extract SKILL.md text between two headings (start inclusive, end exclusive)."""
    pattern = re.escape(start_heading) + r".*?(?=" + re.escape(end_heading) + r")"
    m = re.search(pattern, text, re.DOTALL)
    assert m, f"Could not find section '{start_heading}' before '{end_heading}' in SKILL.md"
    return m.group(0)


# ── Step 1: L1 fail-fast gate ──────────────────────────────────────────────


def test_l1_fail_fast_gate_present(skill_text: str) -> None:
    """Step 1 must document the L1 fail-fast gate with a halt/do-not-proceed instruction."""
    step1 = skill_text_between("### Step 1: L1 Fail-Fast Gate", "### Step 2:", skill_text)
    lower = step1.lower()
    assert "fail-fast" in lower or "fail fast" in lower, (
        "Step 1 must name the L1 fail-fast gate in its heading or body"
    )
    assert "halt" in lower or "do not proceed" in lower, (
        "Step 1 must include a halt/do-not-proceed instruction for STRUCTURAL criticals"
    )


def test_addressable_structural_classification_documented(skill_text: str) -> None:
    """Step 1 must document both ADDRESSABLE and STRUCTURAL classification categories."""
    step1 = skill_text_between("### Step 1: L1 Fail-Fast Gate", "### Step 2:", skill_text)
    assert "ADDRESSABLE" in step1, "Step 1 must document the ADDRESSABLE classification"
    assert "STRUCTURAL" in step1, "Step 1 must document the STRUCTURAL classification"


def test_l1_subagents_described(skill_text: str) -> None:
    """Step 1 must describe both L1 subagents: estimand_clarity and hypothesis_falsifiability."""
    step1 = skill_text_between("### Step 1: L1 Fail-Fast Gate", "### Step 2:", skill_text)
    assert "estimand_clarity" in step1, "Step 1 must name the `estimand_clarity` L1 subagent"
    assert "hypothesis_falsifiability" in step1, (
        "Step 1 must name the `hypothesis_falsifiability` L1 subagent"
    )


# ── Step 2: L2 + red-team ──────────────────────────────────────────────────


def test_l2_red_team_concurrent_dispatch(skill_text: str) -> None:
    """Step 2 must dispatch L2 subagents and the red-team agent in a single parallel message."""
    step2 = skill_text_between("### Step 2:", "### Step 3:", skill_text)
    lower = step2.lower()
    assert "same parallel message" in lower or "concurrent" in lower, (
        "Step 2 must instruct the agent to launch L2 + red-team in the same parallel message"
    )


def test_red_team_five_universal_challenges_present(skill_text: str) -> None:
    """Step 2 must enumerate all five universal red-team challenges."""
    step2 = skill_text_between("### Step 2:", "### Step 3:", skill_text)
    for challenge in ("Goodhart", "leakage", "tuning", "survivorship", "collision"):
        assert challenge in step2, (
            f"Step 2 red-team must include the `{challenge}` universal challenge"
        )


def test_red_team_requires_decision_contract(skill_text: str) -> None:
    """Step 2 must mark every red-team finding as `requires_decision: true`."""
    step2 = skill_text_between("### Step 2:", "### Step 3:", skill_text)
    assert "requires_decision: true" in step2 or '"requires_decision": true' in step2, (
        "Step 2 must declare the `requires_decision: true` contract for red-team findings"
    )


# ── Step 3 / Step 4 ───────────────────────────────────────────────────────


def test_l3_subagents_receive_experiment_type(skill_text: str) -> None:
    """Step 3 subagents must receive the `experiment_type` input."""
    step3 = skill_text_between("### Step 3:", "### Step 4:", skill_text)
    assert "experiment_type" in step3, (
        "Step 3 L3 subagents must receive the `experiment_type` input from classify"
    )


def test_l4_step_has_agent_implementability(skill_text: str) -> None:
    """Step 4 must include the `agent_implementability` subagent in its L4 list."""
    step4 = skill_text_between("### Step 4:", "### Three-Layer Silencing Rules", skill_text)
    assert "agent_implementability" in step4, (
        "Step 4 L4 subagent list must include `agent_implementability`"
    )


def test_agent_implementability_in_l4_step(skill_text: str) -> None:
    """Step 4 must document the `agent_implementability` subagent under its name."""
    step4 = skill_text_between("### Step 4:", "### Three-Layer Silencing Rules", skill_text)
    assert "agent_implementability" in step4, (
        "Step 4 must name the `agent_implementability` subagent in the L4 roster"
    )


def test_agent_implementability_sub_checks_documented(skill_text: str) -> None:
    """Step 4 must document all 7 sub-checks for the agent_implementability subagent."""
    step4 = skill_text_between("### Step 4:", "### Three-Layer Silencing Rules", skill_text)
    # Normalize whitespace so line-wrapped phrases match.
    normalized = " ".join(step4.lower().split())
    sub_checks = [
        "step atomicity",
        "file path resolvability",
        "performance feasibility",
        "verification criteria completeness",
        "dependency ordering",
        "absence of human-only actions",
        "artifact continuity",
    ]
    for check in sub_checks:
        assert check in normalized, (
            f"Step 4 `agent_implementability` subagent must document the sub-check `{check}`"
        )


# ── Silencing rules / scope alignment ──────────────────────────────────────


def test_scope_alignment_scope_report_absent_behavior(skill_text: str) -> None:
    """Silencing rules treat `scope_alignment` as SILENT when `scope_report_path` is absent."""
    silencing = skill_text_between("### Three-Layer Silencing Rules", "### Step 5:", skill_text)
    assert "scope_alignment" in silencing, (
        "Silencing rules must reference the `scope_alignment` dimension"
    )
    assert "scope_report_path" in silencing, (
        "Silencing rules must reference the `scope_report_path` input that gates "
        "the scope_alignment spawn decision"
    )


def test_scope_report_path_argument_with_forwarding(skill_text: str) -> None:
    """`scope_report_path` must be listed in `## Arguments` with a forwarding description."""
    args_section = skill_text_between("## Arguments", "## Critical Constraints", skill_text)
    assert "scope_report_path" in args_section, (
        "`scope_report_path` must be documented in the `## Arguments` section"
    )
    lower = args_section.lower()
    assert "forward" in lower or "thread" in lower or "recipe context" in lower, (
        "`scope_report_path` argument must describe forwarding/threading to "
        "downstream steps (it is a positional `with: args:` value)"
    )


# ── Output tokens ──────────────────────────────────────────────────────────


def test_findings_manifest_path_output_token_documented(skill_text: str) -> None:
    """The SKILL.md must document the `findings_manifest_path` output token."""
    assert "findings_manifest_path" in skill_text, (
        "apply-review-dimensions/SKILL.md must reference the `findings_manifest_path` "
        "output token (consumed by the downstream synthesis step)"
    )


def test_evaluation_dashboard_path_output_token_documented(skill_text: str) -> None:
    """The SKILL.md must document the `evaluation_dashboard_path` output token."""
    assert "evaluation_dashboard_path" in skill_text, (
        "apply-review-dimensions/SKILL.md must reference the `evaluation_dashboard_path` "
        "output token"
    )


def test_no_verdict_token_emitted(skill_text: str) -> None:
    """Step 8 must not emit a `verdict` token (or must explicitly say `no verdict`)."""
    step8 = skill_text_between(
        "### Step 8: Emit Structured Output Tokens", "## Output", skill_text
    )
    lower = step8.lower()
    # The block lists findings_manifest_path and evaluation_dashboard_path only;
    # the explicit "no verdict" / "no `verdict`" language documents the absence.
    assert "no verdict" in lower or "no `verdict`" in lower or "verdict" not in lower, (
        "Step 8 must explicitly not emit a `verdict` token (verdict is computed by "
        "the downstream synthesis step)"
    )


# ── Findings manifest schema ──────────────────────────────────────────────


def test_findings_manifest_json_schema_documented(skill_text: str) -> None:
    """Step 6 must document the findings manifest JSON schema with all required fields."""
    assert '"dimension"' in skill_text or "dimension:" in skill_text, (
        "Findings manifest schema must include a `dimension` field"
    )
    assert '"level"' in skill_text or "level:" in skill_text, (
        "Findings manifest schema must include a `level` field"
    )
    assert '"severity"' in skill_text or "severity:" in skill_text, (
        "Findings manifest schema must include a `severity` field"
    )
    assert '"finding"' in skill_text or "finding:" in skill_text, (
        "Findings manifest schema must include a `finding` field"
    )
    assert '"addressable"' in skill_text or "addressable:" in skill_text, (
        "Findings manifest schema must include an `addressable` field"
    )


# ── Evaluation dashboard ──────────────────────────────────────────────────


def test_evaluation_dashboard_cannot_assess_section(skill_text: str) -> None:
    """Step 7 must include a 'Cannot Assess' section with a minimum-2 entry requirement."""
    step7 = skill_text_between("### Step 7: Write Evaluation Dashboard", "### Step 8:", skill_text)
    assert "Cannot Assess" in step7, (
        "Step 7 must include a `Cannot Assess` section in the evaluation dashboard"
    )
    lower = step7.lower()
    assert "minimum 2" in lower or "at least 2" in lower, (
        "Step 7 must require a minimum of 2 'Cannot Assess' entries"
    )


def test_evaluation_dashboard_yaml_summary_block(skill_text: str) -> None:
    """Step 7 must include the canonical machine-summary YAML header line."""
    assert "# --- apply-review-dimensions machine summary ---" in skill_text, (
        "Step 7 must include the canonical `# --- apply-review-dimensions machine summary ---` "
        "header on the dashboard's machine-readable YAML block"
    )


def test_yaml_summary_includes_active_dimensions(skill_text: str) -> None:
    """The machine-summary YAML block must include an `active_dimensions:` field."""
    assert "active_dimensions:" in skill_text, (
        "Step 7 YAML summary block must include an `active_dimensions:` field"
    )


def test_yaml_summary_no_verdict_field(skill_text: str) -> None:
    """The SKILL.md must explicitly note that the YAML summary block has no `verdict` field."""
    # The NOTE clause immediately after the YAML block must call this out.
    assert "NO `verdict` field" in skill_text or "No `verdict`" in skill_text, (
        "Step 7 must explicitly state that the machine-summary YAML block has no "
        "`verdict` field — verdict is computed downstream by `aggregate_review_verdict`"
    )
