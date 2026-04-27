"""Structural contracts for the investigate deep analysis mode.

Validates that investigate/SKILL.md carries the required instructions
for Deep Analysis Mode: Steps D1–D6, activation conditions, subagent
templates, and enhanced report template fields.
"""

import pytest


@pytest.fixture(scope="module")
def deep_mode_section(skill_text: str) -> str:
    """Extract the ## Deep Analysis Mode section text (up to the next ## heading)."""
    start_idx = skill_text.find("## Deep Analysis Mode")
    if start_idx == -1:
        pytest.fail("'## Deep Analysis Mode' section not found in investigate SKILL.md")
    # Find the next ## heading (not ###) after the section start
    search_from = start_idx + len("## Deep Analysis Mode")
    next_h2 = -1
    for i in range(search_from, len(skill_text) - 2):
        if skill_text[i : i + 3] == "## " and skill_text[i - 1] == "\n":
            next_h2 = i
            break
    if next_h2 != -1:
        return skill_text[start_idx:next_h2]
    return skill_text[start_idx:]


@pytest.fixture(scope="module")
def deep_workflow_section(skill_text: str) -> str:
    """Extract ## Deep Analysis Mode Workflow section to end-of-file (or next ## at depth ≤ 2)."""
    start_idx = skill_text.find("## Deep Analysis Mode Workflow")
    if start_idx == -1:
        pytest.fail("'## Deep Analysis Mode Workflow' not found in investigate SKILL.md")
    search_from = start_idx + len("## Deep Analysis Mode Workflow")
    next_h2 = -1
    for i in range(search_from, len(skill_text) - 2):
        if skill_text[i : i + 3] == "## " and skill_text[i - 1] == "\n":
            next_h2 = i
            break
    if next_h2 != -1:
        return skill_text[start_idx:next_h2]
    return skill_text[start_idx:]


@pytest.fixture(scope="module")
def standard_workflow_section(skill_text: str) -> str:
    """Extract ## Standard Mode Workflow section."""
    start_idx = skill_text.find("## Standard Mode Workflow")
    if start_idx == -1:
        pytest.fail("'## Standard Mode Workflow' not found in investigate SKILL.md")
    deep_workflow_idx = skill_text.find("## Deep Analysis Mode Workflow", start_idx)
    if deep_workflow_idx != -1:
        return skill_text[start_idx:deep_workflow_idx]
    return skill_text[start_idx:]


# ── Deep Mode Activation & Configuration ──────────────────────────────────────


def test_deep_mode_activation_via_depth_flag(deep_mode_section: str) -> None:
    """Deep mode section must document the --depth deep activation flag."""
    assert "--depth deep" in deep_mode_section, (
        "Deep Analysis Mode section must document '--depth deep' as an activation flag"
    )


def test_deep_mode_activation_via_trigger_phrases(deep_mode_section: str) -> None:
    """Deep mode section must document trigger phrases for activation."""
    has_investigate_deeply = "investigate deeply" in deep_mode_section.lower()
    has_deep_analysis = "deep analysis" in deep_mode_section.lower()
    assert has_investigate_deeply or has_deep_analysis, (
        "Deep Analysis Mode section must document activation trigger phrases such as "
        "'investigate deeply' or 'deep analysis'"
    )


def test_deep_mode_not_default(deep_mode_section: str) -> None:
    """Deep mode must not be enabled by default."""
    assert "never enabled by default" in deep_mode_section.lower(), (
        "Deep Analysis Mode section must state that it is 'never enabled by default'"
    )


def test_deep_mode_subagents_use_sonnet(deep_mode_section: str) -> None:
    """Deep mode section must specify that subagents use model: sonnet."""
    assert 'model: "sonnet"' in deep_mode_section, (
        "Deep Analysis Mode section must specify 'model: \"sonnet\"' for all subagents"
    )


# ── Standard Mode Preserved ───────────────────────────────────────────────────


def test_standard_mode_workflow_heading_exists(skill_text: str) -> None:
    """'## Standard Mode Workflow' heading must be present."""
    assert "## Standard Mode Workflow" in skill_text, (
        "investigate SKILL.md must contain a '## Standard Mode Workflow' heading "
        "to clearly separate standard and deep modes"
    )


def test_standard_mode_has_step_1_through_4(standard_workflow_section: str) -> None:
    """Standard mode workflow must contain Steps 1, 2, 3, 3.5, and 4."""
    for step in ("### Step 1:", "### Step 2:", "### Step 3:", "Step 3.5", "### Step 4:"):
        assert step in standard_workflow_section, (
            f"Standard Mode Workflow section must contain '{step}'"
        )


def test_standard_mode_scope_boundary_in_synthesis(standard_workflow_section: str) -> None:
    """Step 3 synthesis list in standard mode must include Scope Boundary."""
    step_3_idx = standard_workflow_section.find("### Step 3:")
    step_35_idx = standard_workflow_section.find("Step 3.5", step_3_idx)
    step_3_text = standard_workflow_section[step_3_idx:step_35_idx]
    assert "Scope Boundary" in step_3_text, (
        "Standard mode Step 3 synthesis list must include 'Scope Boundary' as a finding item"
    )


def test_standard_mode_confidence_levels_in_synthesis(standard_workflow_section: str) -> None:
    """Step 3 synthesis list in standard mode must include Confidence Levels."""
    step_3_idx = standard_workflow_section.find("### Step 3:")
    step_35_idx = standard_workflow_section.find("Step 3.5", step_3_idx)
    step_3_text = standard_workflow_section[step_3_idx:step_35_idx]
    assert "Confidence Levels" in step_3_text, (
        "Standard mode Step 3 synthesis list must include 'Confidence Levels' as a finding item"
    )


# ── Deep Workflow Steps D1–D6 ──────────────────────────────────────────────────


@pytest.mark.parametrize("step", ["D1", "D2", "D3", "D4", "D5", "D6"])
def test_deep_workflow_has_step(deep_workflow_section: str, step: str) -> None:
    """Deep workflow must contain each D-step heading."""
    assert f"### Step {step}" in deep_workflow_section, (
        f"Deep Analysis Mode Workflow must contain a '### Step {step}' heading"
    )


def _extract_step_section(deep_workflow_section: str, step_name: str) -> str:
    """Helper to extract the text of a specific D-step subsection."""
    start = deep_workflow_section.find(f"### {step_name}")
    if start == -1:
        return ""
    next_step = deep_workflow_section.find("### Step D", start + 1)
    if next_step != -1:
        return deep_workflow_section[start:next_step]
    return deep_workflow_section[start:]


def test_d1_batch_plan_proposal(deep_workflow_section: str) -> None:
    """Step D1 must describe proposing a batch plan."""
    d1 = _extract_step_section(deep_workflow_section, "Step D1")
    has_batch_plan = "batch plan" in d1.lower()
    has_proposed = "Proposed investigation plan" in d1
    assert has_batch_plan or has_proposed, (
        "Step D1 must describe proposing a 'batch plan' or 'Proposed investigation plan'"
    )


def test_d1_headless_auto_approve(deep_workflow_section: str) -> None:
    """Step D1 must reference AUTOSKILLIT_HEADLESS for auto-approve."""
    d1 = _extract_step_section(deep_workflow_section, "Step D1")
    assert "AUTOSKILLIT_HEADLESS" in d1, (
        "Step D1 must reference 'AUTOSKILLIT_HEADLESS' environment variable for "
        "headless auto-approval of the batch plan"
    )


def test_d2_broad_exploration_minimum_subagents(deep_workflow_section: str) -> None:
    """Step D2 must specify a minimum of 5 parallel subagents."""
    d2 = _extract_step_section(deep_workflow_section, "Step D2")
    has_minimum = "minimum" in d2.lower()
    has_five = "5" in d2
    assert has_minimum and has_five, (
        "Step D2 must specify a 'minimum' of '5' parallel subagents for broad exploration"
    )


def test_d2_inter_batch_synthesis(deep_workflow_section: str) -> None:
    """Step D2 must describe inter-batch synthesis."""
    d2 = _extract_step_section(deep_workflow_section, "Step D2")
    assert "inter-batch synthesis" in d2.lower(), (
        "Step D2 must describe 'inter-batch synthesis' after Batch 1 completes"
    )


def test_d2_historical_recurrence_parallel(deep_workflow_section: str) -> None:
    """Step D2 must reference Step 3.5 or historical check running in parallel."""
    d2 = _extract_step_section(deep_workflow_section, "Step D2")
    has_35 = "3.5" in d2
    has_historical = "historical" in d2.lower()
    assert has_35 or has_historical, (
        "Step D2 must reference '3.5' or 'historical' check running in parallel with Batch 1"
    )


def test_d3_mandatory_web_research(deep_workflow_section: str) -> None:
    """Step D3 must require web research in each batch."""
    d3 = _extract_step_section(deep_workflow_section, "Step D3")
    assert "web research" in d3.lower(), (
        "Step D3 must describe mandatory 'web research' in each deepening batch"
    )


def test_d3_mandatory_code_exploration(deep_workflow_section: str) -> None:
    """Step D3 must require code exploration in each batch."""
    d3 = _extract_step_section(deep_workflow_section, "Step D3")
    has_code_exploration = "code exploration" in d3.lower()
    has_local_code = "local code" in d3.lower()
    assert has_code_exploration or has_local_code, (
        "Step D3 must describe mandatory 'code exploration' or 'local code' search "
        "in each deepening batch"
    )


def test_d3_early_termination(deep_workflow_section: str) -> None:
    """Step D3 must describe early termination conditions."""
    d3 = _extract_step_section(deep_workflow_section, "Step D3")
    assert "early termination" in d3.lower(), (
        "Step D3 must describe 'early termination' conditions for when all findings "
        "are SUPPORTED and no new leads emerge"
    )


def test_d4_challenge_round_adversarial(deep_workflow_section: str) -> None:
    """Step D4 must describe an adversarial subagent."""
    d4 = _extract_step_section(deep_workflow_section, "Step D4")
    assert "adversarial" in d4.lower(), (
        "Step D4 must describe an 'adversarial' subagent for the challenge round"
    )


def test_d4_needs_evidence_trigger(deep_workflow_section: str) -> None:
    """Step D4 must be triggered by NEEDS-EVIDENCE findings."""
    d4 = _extract_step_section(deep_workflow_section, "Step D4")
    assert "NEEDS-EVIDENCE" in d4, (
        "Step D4 must specify that it fires when a finding is marked 'NEEDS-EVIDENCE'"
    )


def test_d4_prior_fix_falsifiability(deep_workflow_section: str) -> None:
    """Step D4 must reference prior fix falsifiability check."""
    d4 = _extract_step_section(deep_workflow_section, "Step D4")
    has_falsifiability = "falsifiability" in d4.lower()
    has_prior_fix = "prior fix" in d4.lower()
    assert has_falsifiability or has_prior_fix, (
        "Step D4 must describe 'falsifiability' check or 'prior fix' assessment"
    )


def test_d5_blast_radius(deep_workflow_section: str) -> None:
    """Step D5 must describe blast radius analysis."""
    d5 = _extract_step_section(deep_workflow_section, "Step D5")
    assert "blast radius" in d5.lower(), (
        "Step D5 must describe 'blast radius' analysis for candidate solutions"
    )


def test_d5_single_recommendation(deep_workflow_section: str) -> None:
    """Step D5 must converge to a single recommendation."""
    d5 = _extract_step_section(deep_workflow_section, "Step D5")
    assert "single recommendation" in d5.lower(), (
        "Step D5 must converge to a 'single recommendation' after blast radius analysis"
    )


def test_d6_post_report_validation(deep_workflow_section: str) -> None:
    """Step D6 must describe post-report validation."""
    d6 = _extract_step_section(deep_workflow_section, "Step D6")
    assert "validation" in d6.lower(), "Step D6 must describe post-report 'validation'"


def test_d6_independent_validators(deep_workflow_section: str) -> None:
    """Step D6 must specify 2 or 3 independent validators."""
    d6 = _extract_step_section(deep_workflow_section, "Step D6")
    has_count = "2" in d6 or "3" in d6
    has_validator = "validator" in d6.lower()
    assert has_count and has_validator, (
        "Step D6 must specify '2' or '3' independent 'validator' subagents"
    )


def test_d6_factual_accuracy_check(deep_workflow_section: str) -> None:
    """Step D6 must include a factual accuracy check."""
    d6 = _extract_step_section(deep_workflow_section, "Step D6")
    assert "factual accuracy" in d6.lower(), (
        "Step D6 must include a 'factual accuracy' validator role"
    )


# ── Enhanced Report Template ───────────────────────────────────────────────────


def test_report_mode_field(report_section: str) -> None:
    """Step 4 report template must include a Mode field."""
    assert "Mode" in report_section, (
        "Step 4 report template must include a '**Mode:**' field in the report header"
    )


def test_report_scope_boundary_section(report_section: str) -> None:
    """Step 4 report template must include a Scope Boundary section."""
    assert "Scope Boundary" in report_section, (
        "Step 4 report template must include a '## Scope Boundary' section"
    )


def test_report_confidence_levels(report_section: str) -> None:
    """Step 4 report template must show SUPPORTED, UNSUPPORTED, and NEEDS-EVIDENCE."""
    assert "SUPPORTED" in report_section, (
        "Step 4 report template must reference 'SUPPORTED' confidence label"
    )
    assert "UNSUPPORTED" in report_section or "NEEDS-EVIDENCE" in report_section, (
        "Step 4 report template must reference 'UNSUPPORTED' or 'NEEDS-EVIDENCE' confidence labels"
    )


def test_report_no_auto_file_issues(skill_text: str) -> None:
    """NEVER constraints must prohibit automatic GitHub issue filing."""
    never_idx = skill_text.find("**NEVER:**")
    assert never_idx != -1, "NEVER constraints block not found in investigate SKILL.md"
    # Find the ALWAYS block to bound the search
    always_idx = skill_text.find("**ALWAYS:**", never_idx)
    never_block = skill_text[never_idx:always_idx] if always_idx != -1 else skill_text[never_idx:]
    has_issue = "issue" in never_block.lower()
    has_prohibition = "automatically" in never_block.lower()
    assert has_issue and has_prohibition, (
        "NEVER constraints must prohibit filing GitHub issues automatically"
    )


# ── Subagent Templates ─────────────────────────────────────────────────────────


def test_deep_mode_subagent_template_exists(skill_text: str) -> None:
    """SKILL.md must contain a Deep Analysis Mode Template section."""
    assert "Deep Analysis Mode Template" in skill_text, (
        "investigate SKILL.md must contain a 'Deep Analysis Mode Template' section "
        "under the Subagent Prompt Template heading"
    )


def test_adversarial_subagent_template_exists(skill_text: str) -> None:
    """SKILL.md must contain an Adversarial Subagent Template section."""
    assert "Adversarial Subagent Template" in skill_text, (
        "investigate SKILL.md must contain an 'Adversarial Subagent Template' section "
        "for the D4 challenge round"
    )


def test_validation_subagent_template_exists(skill_text: str) -> None:
    """SKILL.md must contain a Validation Subagent Template section."""
    assert "Validation Subagent Template" in skill_text, (
        "investigate SKILL.md must contain a 'Validation Subagent Template' section "
        "for the D6 post-report validators"
    )


def test_deep_template_has_evidence_standards(skill_text: str) -> None:
    """Deep Analysis Mode Template must include Evidence standards block."""
    deep_template_idx = skill_text.find("Deep Analysis Mode Template")
    if deep_template_idx == -1:
        pytest.fail("'Deep Analysis Mode Template' not found in investigate SKILL.md")
    adversarial_idx = skill_text.find("Adversarial Subagent Template", deep_template_idx)
    template_section = (
        skill_text[deep_template_idx:adversarial_idx]
        if adversarial_idx != -1
        else skill_text[deep_template_idx:]
    )
    assert "Evidence standards" in template_section, (
        "Deep Analysis Mode Template must include an 'Evidence standards' block "
        "instructing subagents to cite files, lines, and mark confidence levels"
    )


def test_deep_template_has_inter_batch_context(skill_text: str) -> None:
    """Deep Analysis Mode Template must reference prior batch context."""
    deep_template_idx = skill_text.find("Deep Analysis Mode Template")
    if deep_template_idx == -1:
        pytest.fail("'Deep Analysis Mode Template' not found in investigate SKILL.md")
    adversarial_idx = skill_text.find("Adversarial Subagent Template", deep_template_idx)
    template_section = (
        skill_text[deep_template_idx:adversarial_idx]
        if adversarial_idx != -1
        else skill_text[deep_template_idx:]
    )
    has_prior_batches = "prior batches" in template_section.lower()
    has_previous_batch = "previous batch" in template_section.lower()
    assert has_prior_batches or has_previous_batch, (
        "Deep Analysis Mode Template must include a placeholder for context from "
        "'prior batches' or 'previous batch' inter-batch synthesis"
    )
