"""Contract tests verifying adversarial review pass (Steps 6-9) exists in make-plan SKILL.md."""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def make_plan_text() -> str:
    p = pkg_root() / "skills_extended" / "make-plan" / "SKILL.md"
    return p.read_text()


def test_make_plan_step6_adversarial_review_exists(make_plan_text: str) -> None:
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "foundation auditor" in step6_section, "Step 6 must include Foundation Auditor"
    assert "scope expansion" in step6_section, (
        "Step 6 must restrict agents from suggesting scope expansion"
    )
    assert "junior reviewer" in step6_section, "Step 6 must include contrastive prompt frame"


def test_make_plan_step8_registry_trace_exists(make_plan_text: str) -> None:
    step8_idx = make_plan_text.find("**Registry Trace")
    assert step8_idx != -1, "Step 8 must exist with 'Registry Trace' heading"
    step9_idx = make_plan_text.find("**Plan Revision", step8_idx)
    assert step9_idx != -1
    step8_section = make_plan_text[step8_idx:step9_idx].upper()
    sync_patterns = [
        "RETIRED",
        "RE-EXPORT",
        "RULE REGISTRATION",
        "IMPORT LAYER",
        "TYPED ALIASES",
        "DERIVED ARTIFACTS",
    ]
    found = sum(1 for p in sync_patterns if p in step8_section)
    gated_tools = "GATED_TOOLS" in step8_section or "TOOL REGISTRIES" in step8_section
    dual_copy = "DUAL-COPY" in step8_section or "SKILL_FILE_ADVISORY_MAP" in step8_section
    found += int(gated_tools) + int(dual_copy)
    assert found >= 7, f"Step 8 must contain at least 7 of 8 sync pattern names, found {found}"


def test_make_plan_step9_plan_revision_exists(make_plan_text: str) -> None:
    step9_idx = make_plan_text.find("**Plan Revision")
    assert step9_idx != -1, "Step 9 must exist with 'Plan Revision' heading"
    next_heading = make_plan_text.find("\n##", step9_idx)
    end = next_heading if next_heading != -1 else len(make_plan_text)
    step9_section = make_plan_text[step9_idx:end].lower()
    assert "revis" in step9_section, "Step 9 must contain revision-related language"
    assert ("three" in step9_section or "3" in step9_section) and (
        "report" in step9_section or "finding" in step9_section
    ), "Step 9 must reference reading the three adversarial reports"


def test_make_plan_checklist_includes_adversarial_review(make_plan_text: str) -> None:
    checklist_idx = make_plan_text.find("## Skill Loading Checklist")
    assert checklist_idx != -1
    next_heading = make_plan_text.find("\n## ", checklist_idx + 1)
    end = next_heading if next_heading != -1 else len(make_plan_text)
    checklist_section = make_plan_text[checklist_idx:end].lower()
    assert "adversarial" in checklist_section, (
        "Skill Loading Checklist must include an adversarial review checklist item"
    )


def test_make_plan_steps_6_through_9_ordered(make_plan_text: str) -> None:
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    step8_idx = make_plan_text.find("**Registry Trace", planning_idx)
    step9_idx = make_plan_text.find("**Plan Revision", planning_idx)
    assert all(i != -1 for i in (step6_idx, step7_idx, step8_idx, step9_idx))
    assert step6_idx < step7_idx < step8_idx < step9_idx, (
        "Steps 6-9 must appear in order in the document"
    )


def test_make_plan_contains_complexity_gate(make_plan_text: str) -> None:
    """T4.1: make-plan SKILL.md contains adversarial_review_level complexity gate."""
    assert "adversarial_review_level" in make_plan_text, (
        "make-plan SKILL.md must contain 'adversarial_review_level'"
    )


def test_make_plan_contains_complexity_gate_text(make_plan_text: str) -> None:
    """T4.2: make-plan SKILL.md contains complexity classification text."""
    lower_text = make_plan_text.lower()
    assert "complexity gate" in lower_text or "complexity estimation" in lower_text, (
        "make-plan SKILL.md must contain 'Complexity Gate' or 'complexity estimation' string"
    )


def test_plan_registry_tracer_consolidated_script_instruction() -> None:
    """T4.3: plan-registry-tracer.md contains 'single consolidated script' instruction."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "agents" / "plan-registry-tracer.md").read_text()
    assert "single consolidated script" in content, (
        "plan-registry-tracer.md must contain 'single consolidated script' instruction"
    )


def test_plan_registry_tracer_no_old_per_symbol_pattern() -> None:
    """T5.1: plan-registry-tracer.md does not contain old 'For EACH symbol' pattern."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "agents" / "plan-registry-tracer.md").read_text()
    assert "For EACH symbol from Step 1" not in content, (
        "plan-registry-tracer.md must not contain 'For EACH symbol from Step 1' pattern"
    )


def test_plan_registry_tracer_turn_budget_instruction() -> None:
    """T5.2: plan-registry-tracer.md contains 1-2 Bash tool calls turn budget."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "agents" / "plan-registry-tracer.md").read_text()
    assert "1-2 Bash tool" in content or ("1-2" in content and "Bash" in content), (
        "plan-registry-tracer.md must contain '1-2 Bash tool calls' turn budget instruction"
    )


def test_make_plan_steps_5e_through_9_ordered(make_plan_text: str) -> None:
    """Verify Steps 5e and 6-9 appear in order in the document."""
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step5e_idx = make_plan_text.find(
        "**5e. Complexity-Gated Adversarial Review Decision", planning_idx
    )
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    step8_idx = make_plan_text.find("**Registry Trace", planning_idx)
    step9_idx = make_plan_text.find("**Plan Revision", planning_idx)
    assert step5e_idx != -1, (
        "Step 5e must exist with 'Complexity-Gated Adversarial Review Decision' heading"
    )
    assert all(i != -1 for i in (step6_idx, step7_idx, step8_idx, step9_idx))
    assert step5e_idx < step6_idx < step7_idx < step8_idx < step9_idx, (
        "Steps 5e-9 must appear in order in the document"
    )


def test_make_plan_interface_mapping_and_registry_trace_responsibilities(
    make_plan_text: str,
) -> None:
    """Verify Steps 7 and 8 cover their respective responsibilities."""
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1

    # Step 7 (Interface Mapping) must contain variable/SET/READ responsibility
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    step8_idx = make_plan_text.find("**Registry Trace", planning_idx)
    assert step7_idx != -1 and step8_idx != -1
    step7_section = make_plan_text[step7_idx:step8_idx].lower()
    assert "variable" in step7_section or "set/read" in step7_section, (
        "Step 7 (Interface Mapping) must mention variable/SET/READ tracing"
    )
    assert "junior reviewer" in step7_section, "Step 7 must include contrastive prompt frame"

    # Step 8 (Registry Trace) must contain registry/symbol responsibility
    step9_idx = make_plan_text.find("**Plan Revision", step8_idx)
    assert step9_idx != -1
    step8_section = make_plan_text[step8_idx:step9_idx].lower()
    assert "registry" in step8_section or "symbol" in step8_section, (
        "Step 8 (Registry Trace) must mention registry/symbol tracing"
    )
    assert "junior reviewer" in step8_section, "Step 8 must include contrastive prompt frame"
