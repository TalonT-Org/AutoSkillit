"""Contract tests for Design Intent Analysis requirements in the investigate skill.

REQ-INTENT-001: Design Intent subagent — proactively discovers why mechanisms exist
               and what depends on them (standard and deep modes).
REQ-INTENT-002: Adversarial Breakage subagent — challenges removal/change recommendations
               by tracing what would break (deep mode only).
"""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def skill_text() -> str:
    path = pkg_root() / "skills_extended" / "investigate" / "SKILL.md"
    return path.read_text()


@pytest.fixture(scope="module")
def standard_workflow_section(skill_text: str) -> str:
    start = skill_text.find("## Standard Mode Workflow")
    if start == -1:
        return ""
    deep_start = skill_text.find("## Deep Analysis Mode Workflow", start + 1)
    if deep_start != -1:
        return skill_text[start:deep_start]
    return skill_text[start:]


@pytest.fixture(scope="module")
def deep_workflow_section(skill_text: str) -> str:
    start = skill_text.find("## Deep Analysis Mode Workflow")
    if start == -1:
        return ""
    # Find next ## heading at depth 2
    pos = start + len("## Deep Analysis Mode Workflow")
    while pos < len(skill_text):
        newline = skill_text.find("\n## ", pos)
        if newline == -1:
            return skill_text[start:]
        return skill_text[start : newline + 1]
    return skill_text[start:]


@pytest.fixture(scope="module")
def step2_section(skill_text: str) -> str:
    start = skill_text.find("### Step 2:")
    if start == -1:
        return ""
    end = skill_text.find("### Step 3:", start + 1)
    if end != -1:
        return skill_text[start:end]
    return skill_text[start:]


@pytest.fixture(scope="module")
def design_intent_content(step2_section: str) -> str:
    start = step2_section.find("**Design Intent**")
    if start == -1:
        return ""
    # Find next bold header or end of step2
    next_header = step2_section.find("\n**", start + 1)
    if next_header != -1:
        return step2_section[start:next_header]
    return step2_section[start:]


def _extract_step_section(deep_workflow_section: str, step_name: str) -> str:
    """Helper to extract the text of a specific D-step subsection."""
    start = deep_workflow_section.find(f"### {step_name}")
    if start == -1:
        return ""
    next_step = deep_workflow_section.find("### Step D", start + 1)
    if next_step != -1:
        return deep_workflow_section[start:next_step]
    return deep_workflow_section[start:]


@pytest.fixture(scope="module")
def d3_section(deep_workflow_section: str) -> str:
    return _extract_step_section(deep_workflow_section, "Step D3")


@pytest.fixture(scope="module")
def d5_section(deep_workflow_section: str) -> str:
    return _extract_step_section(deep_workflow_section, "Step D5")


# ── REQ-INTENT-001: Design Intent Subagent (Standard Mode) ───────────────────


def test_step2_has_design_intent_vector(step2_section: str) -> None:
    """Step 2 section must contain Design Intent as a parallel investigation vector."""
    assert "Design Intent" in step2_section, (
        "Step 2 must include 'Design Intent' as a parallel investigation vector"
    )


def test_design_intent_uses_git_log_follow(design_intent_content: str) -> None:
    """Design Intent content must contain 'git log --follow'."""
    assert "git log --follow" in design_intent_content, (
        "Design Intent vector must use 'git log --follow' to trace the introducing commit"
    )


def test_design_intent_traces_callers_dependents(design_intent_content: str) -> None:
    """Design Intent content must mention callers/dependents tracing."""
    has_callers = "callers" in design_intent_content.lower()
    has_dependents = "dependents" in design_intent_content.lower()
    assert has_callers or has_dependents, (
        "Design Intent vector must mention tracing callers or dependents"
    )


def test_design_intent_checks_architecture_docs(design_intent_content: str) -> None:
    """Design Intent content must mention architecture docs."""
    has_arch = "architecture" in design_intent_content.lower()
    has_claude_md = "CLAUDE.md" in design_intent_content
    has_adr = "ADR" in design_intent_content
    assert has_arch or has_claude_md or has_adr, (
        "Design Intent vector must mention architecture docs (architecture.md, CLAUDE.md, or ADRs)"
    )


def test_design_intent_produces_finding_per_mechanism(design_intent_content: str) -> None:
    """Design Intent content must mention 'design intent finding'."""
    assert "design intent finding" in design_intent_content.lower(), (
        "Design Intent vector must produce a 'design intent finding' per mechanism"
    )


def test_step3_synthesis_includes_design_intent_findings(skill_text: str) -> None:
    """Step 3 numbered list must include 'Design Intent Findings'."""
    start = skill_text.find("### Step 3:")
    end = skill_text.find("### Step 3.5", start + 1)
    step3 = skill_text[start:end] if end != -1 else skill_text[start:]
    assert "Design Intent Findings" in step3, (
        "Step 3 synthesis list must include 'Design Intent Findings'"
    )


def test_report_template_has_design_intent_section(skill_text: str) -> None:
    """Step 4 report template must include '## Design Intent Findings'."""
    assert "## Design Intent Findings" in skill_text, (
        "Step 4 report template must include '## Design Intent Findings' section"
    )


def test_design_intent_section_positioned_after_similar_patterns(skill_text: str) -> None:
    """Design Intent Findings must appear after Similar Patterns in the report template."""
    similar_pos = skill_text.find("## Similar Patterns")
    design_intent_pos = skill_text.find("## Design Intent Findings")
    historical_pos = skill_text.find("## Historical Context")
    assert similar_pos != -1 and design_intent_pos != -1, (
        "Both '## Similar Patterns' and '## Design Intent Findings' must exist in report template"
    )
    assert similar_pos < design_intent_pos, (
        "'## Design Intent Findings' must appear after '## Similar Patterns' in report template"
    )
    assert design_intent_pos < historical_pos, (
        "'## Design Intent Findings' must appear before '## Historical Context' in report template"
    )


# ── REQ-INTENT-001: Design Intent Subagent (Deep Mode) ───────────────────────


def test_d2_includes_design_intent_subagent(deep_workflow_section: str) -> None:
    """Step D2 must mention Design Intent as a parallel subagent."""
    d2 = _extract_step_section(deep_workflow_section, "Step D2")
    assert "Design Intent" in d2, (
        "Step D2 must include 'Design Intent' as a parallel subagent"
    )


def test_d2_minimum_subagents_increased(deep_workflow_section: str) -> None:
    """Step D2 must specify a minimum of 5 parallel subagents."""
    d2 = _extract_step_section(deep_workflow_section, "Step D2")
    has_minimum = "minimum" in d2.lower()
    has_five = "5" in d2
    assert has_minimum and has_five, (
        "Step D2 must specify a 'minimum' of '5' parallel subagents (was 4, increased for Design Intent)"
    )


def test_d3_design_intent_redispatch(d3_section: str) -> None:
    """Step D3 must describe re-dispatching Design Intent when new mechanisms surface."""
    has_design_intent = "Design Intent" in d3_section
    has_redispatch = "re-dispatch" in d3_section.lower() or "redispatch" in d3_section.lower()
    assert has_design_intent and has_redispatch, (
        "Step D3 must mention re-dispatching the Design Intent subagent for newly surfaced mechanisms"
    )


# ── REQ-INTENT-002: Adversarial Breakage Analysis (Deep Mode Only) ────────────


def test_d5_has_adversarial_breakage_subagent(d5_section: str) -> None:
    """Step D5 must contain adversarial breakage or breakage analysis."""
    has_adversarial = "adversarial breakage" in d5_section.lower()
    has_breakage = "breakage analysis" in d5_section.lower()
    assert has_adversarial or has_breakage, (
        "Step D5 must include 'adversarial breakage' or 'breakage analysis'"
    )


def test_d5_breakage_triggers_on_removal_change(d5_section: str) -> None:
    """Step D5 breakage must fire on removal/replacement/change recommendations."""
    has_removal = "removal" in d5_section.lower()
    has_replacement = "replacement" in d5_section.lower()
    has_change = "change" in d5_section.lower()
    assert has_removal or has_replacement or has_change, (
        "Step D5 breakage analysis must trigger on removal, replacement, or change recommendations"
    )


def test_d5_breakage_traces_dependency_chain(d5_section: str) -> None:
    """Step D5 breakage must mention dependency chain tracing (callers, importers, flag consumers)."""
    has_dependency = "dependency chain" in d5_section.lower()
    has_callers = "callers" in d5_section.lower()
    has_importers = "importers" in d5_section.lower()
    assert has_dependency or has_callers or has_importers, (
        "Step D5 breakage analysis must mention dependency chain tracing (callers, importers, flag consumers)"
    )


def test_d5_breakage_checks_revert_patterns(d5_section: str) -> None:
    """Step D5 breakage must mention checking git history for revert patterns."""
    has_revert = "revert" in d5_section.lower()
    has_git = "git" in d5_section.lower()
    assert has_revert and has_git, (
        "Step D5 breakage analysis must mention checking git history for revert patterns"
    )


def test_d5_breakage_distinct_from_d4(deep_workflow_section: str) -> None:
    """D4 is epistemological (hypothesis/root cause); D5 breakage is consequentialist (recommendation/breaks)."""
    d4 = _extract_step_section(deep_workflow_section, "Step D4")
    d5 = _extract_step_section(deep_workflow_section, "Step D5")
    # D4: epistemological — about hypothesis/root cause
    d4_epistemic = "hypothesis" in d4.lower() or "root cause" in d4.lower()
    # D5: consequentialist — about recommendation and what breaks
    d5_consequentialist = "recommendation" in d5.lower() and "break" in d5.lower()
    assert d4_epistemic, "D4 must mention 'hypothesis' or 'root cause' (epistemological trigger)"
    assert d5_consequentialist, (
        "D5 must mention 'recommendation' and 'break' (consequentialist trigger, distinct from D4)"
    )


def test_report_template_has_breakage_analysis_deep_mode(skill_text: str) -> None:
    """Step 4 report template must include breakage analysis for deep mode."""
    has_breakage = "## Breakage Analysis" in skill_text
    assert has_breakage, (
        "Step 4 report template must include '## Breakage Analysis' section for deep mode"
    )


# ── Regression Guards ─────────────────────────────────────────────────────────


def test_standard_mode_no_adversarial_breakage(standard_workflow_section: str) -> None:
    """Standard mode workflow must NOT contain adversarial breakage content."""
    has_adversarial = "adversarial breakage" in standard_workflow_section.lower()
    has_breakage_analysis = "breakage analysis" in standard_workflow_section.lower()
    assert not has_adversarial and not has_breakage_analysis, (
        "Standard mode workflow must not contain 'adversarial breakage' or 'breakage analysis'"
    )


def test_investigation_path_output_contract_unchanged(skill_text: str) -> None:
    """SKILL.md must still contain the investigation_path output token pattern."""
    assert "investigation_path = " in skill_text, (
        "SKILL.md must still contain 'investigation_path = ' structured output token"
    )


# ── Subagent Templates ────────────────────────────────────────────────────────


def test_design_intent_subagent_template_exists(skill_text: str) -> None:
    """'Design Intent Subagent Template' heading must exist in SKILL.md."""
    assert "Design Intent Subagent Template" in skill_text, (
        "SKILL.md must contain a 'Design Intent Subagent Template' heading"
    )


def test_design_intent_template_has_git_log_follow(skill_text: str) -> None:
    """Design Intent template must contain 'git log --follow'."""
    template_start = skill_text.find("Design Intent Subagent Template")
    if template_start == -1:
        pytest.fail("Design Intent Subagent Template section not found")
    # Find the next template heading after this one
    next_template = skill_text.find("###", template_start + 1)
    template_section = skill_text[template_start:next_template] if next_template != -1 else skill_text[template_start:]
    assert "git log --follow" in template_section, (
        "Design Intent Subagent Template must contain 'git log --follow'"
    )


def test_adversarial_breakage_subagent_template_exists(skill_text: str) -> None:
    """'Adversarial Breakage Subagent Template' heading must exist in SKILL.md."""
    assert "Adversarial Breakage Subagent Template" in skill_text, (
        "SKILL.md must contain an 'Adversarial Breakage Subagent Template' heading"
    )


def test_adversarial_breakage_template_has_dependency_chain(skill_text: str) -> None:
    """Adversarial Breakage template must mention dependency chain."""
    template_start = skill_text.find("Adversarial Breakage Subagent Template")
    if template_start == -1:
        pytest.fail("Adversarial Breakage Subagent Template section not found")
    next_template = skill_text.find("###", template_start + 1)
    template_section = skill_text[template_start:next_template] if next_template != -1 else skill_text[template_start:]
    assert "dependency chain" in template_section.lower(), (
        "Adversarial Breakage Subagent Template must mention dependency chain"
    )


def test_adversarial_breakage_template_has_revert_check(skill_text: str) -> None:
    """Adversarial Breakage template must mention revert pattern check."""
    template_start = skill_text.find("Adversarial Breakage Subagent Template")
    if template_start == -1:
        pytest.fail("Adversarial Breakage Subagent Template section not found")
    next_template = skill_text.find("###", template_start + 1)
    template_section = skill_text[template_start:next_template] if next_template != -1 else skill_text[template_start:]
    assert "revert" in template_section.lower(), (
        "Adversarial Breakage Subagent Template must mention revert pattern check"
    )
