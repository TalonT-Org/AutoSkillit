"""Contract tests verifying adversarial review agents exist in make-plan and rectify."""

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


@pytest.fixture(scope="module")
def make_plan_text() -> str:
    p = pkg_root() / "skills_extended" / "make-plan" / "SKILL.md"
    return p.read_text()


@pytest.fixture(scope="module")
def rectify_text() -> str:
    p = pkg_root() / "skills_extended" / "rectify" / "SKILL.md"
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
    assert "1-2 Bash tool" in content, (
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


# ---------------------------------------------------------------------------
# Rectify adversarial review contract tests (T1–T8)
# ---------------------------------------------------------------------------


def test_rectify_foundation_audit_exists(rectify_text: str) -> None:
    """Rectify SKILL.md must include Foundation Audit step with contrastive prompt."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    fa_idx = rectify_text.find("Foundation Audit", workflow_idx)
    assert fa_idx != -1, "Rectify must include Foundation Audit step"
    im_idx = rectify_text.find("Interface Mapping", fa_idx)
    assert im_idx != -1
    section = rectify_text[fa_idx:im_idx].lower()
    assert "foundation auditor" in section or "plan-foundation-auditor" in section
    assert "scope expansion" in section
    assert "junior engineer" in section


def test_rectify_interface_mapping_exists(rectify_text: str) -> None:
    """Rectify SKILL.md must include Interface Mapping step with contrastive prompt."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    im_idx = rectify_text.find("Interface Mapping", workflow_idx)
    assert im_idx != -1, "Rectify must include Interface Mapping step"
    rt_idx = rectify_text.find("Registry Trace", im_idx)
    assert rt_idx != -1
    section = rectify_text[im_idx:rt_idx].lower()
    assert "variable" in section or "set/read" in section
    assert "junior engineer" in section


def test_rectify_registry_trace_exists(rectify_text: str) -> None:
    """Rectify SKILL.md must include Registry Trace step with sync pattern names."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    rt_idx = rectify_text.find("Registry Trace", workflow_idx)
    assert rt_idx != -1, "Rectify must include Registry Trace step"
    next_heading = rectify_text.find("\n## ", rt_idx)
    end = next_heading if next_heading != -1 else len(rectify_text)
    section = rectify_text[rt_idx:end].upper()
    sync_patterns = [
        "RETIRED",
        "RE-EXPORT",
        "RULE REGISTRATION",
        "IMPORT LAYER",
        "TYPED ALIASES",
        "DERIVED ARTIFACTS",
    ]
    found = sum(1 for p in sync_patterns if p in section)
    gated_tools = "GATED_TOOLS" in section or "TOOL REGISTRIES" in section
    dual_copy = "DUAL-COPY" in section or "SKILL_FILE_ADVISORY_MAP" in section
    found += int(gated_tools) + int(dual_copy)
    assert found >= 7, (
        f"Registry Trace must contain at least 7 of 8 sync pattern names, found {found}"
    )


def test_rectify_adversarial_steps_ordered(rectify_text: str) -> None:
    """Foundation Audit, Interface Mapping, Registry Trace must appear in order."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    fa_idx = rectify_text.find("Foundation Audit", workflow_idx)
    im_idx = rectify_text.find("Interface Mapping", workflow_idx)
    rt_idx = rectify_text.find("Registry Trace", workflow_idx)
    assert all(i != -1 for i in (fa_idx, im_idx, rt_idx)), (
        "All three adversarial steps must exist in Rectify Workflow"
    )
    assert fa_idx < im_idx < rt_idx, (
        "Adversarial steps must appear in order: "
        "Foundation Audit < Interface Mapping < Registry Trace"
    )


def test_rectify_checklist_includes_adversarial_review(rectify_text: str) -> None:
    """Rectify Skill Loading Checklist must include adversarial review item."""
    checklist_idx = rectify_text.find("## Skill Loading Checklist")
    assert checklist_idx != -1
    next_heading = rectify_text.find("\n## ", checklist_idx + 1)
    end = next_heading if next_heading != -1 else len(rectify_text)
    section = rectify_text[checklist_idx:end].lower()
    assert "adversarial" in section, (
        "Skill Loading Checklist must include an adversarial review checklist item"
    )


def test_rectify_interface_mapping_rules(rectify_text: str) -> None:
    """Interface Mapping step must contain RULES FOR APPLYING INTERFACE MAPPING FINDINGS."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    im_idx = rectify_text.find("Interface Mapping", workflow_idx)
    assert im_idx != -1
    rt_idx = rectify_text.find("Registry Trace", im_idx)
    assert rt_idx != -1
    section = rectify_text[im_idx:rt_idx]
    assert "RULES FOR APPLYING INTERFACE MAPPING FINDINGS" in section


def test_rectify_registry_trace_rules(rectify_text: str) -> None:
    """Registry Trace step must contain RULES FOR APPLYING REGISTRY TRACE FINDINGS."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    rt_idx = rectify_text.find("Registry Trace", workflow_idx)
    assert rt_idx != -1
    next_heading = rectify_text.find("\n## ", rt_idx)
    end = next_heading if next_heading != -1 else len(rectify_text)
    section = rectify_text[rt_idx:end]
    assert "RULES FOR APPLYING REGISTRY TRACE FINDINGS" in section


def test_rectify_sequential_revision_pattern(rectify_text: str) -> None:
    """Each adversarial step must instruct revision before the next step."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    fa_idx = rectify_text.find("Foundation Audit", workflow_idx)
    im_idx = rectify_text.find("Interface Mapping", workflow_idx)
    rt_idx = rectify_text.find("Registry Trace", workflow_idx)
    assert all(i != -1 for i in (fa_idx, im_idx, rt_idx))
    fa_section = rectify_text[fa_idx:im_idx].lower()
    im_section = rectify_text[im_idx:rt_idx].lower()
    assert "revis" in fa_section, "Foundation Audit step must include revision instruction"
    assert "revis" in im_section, "Interface Mapping step must include revision instruction"


def test_make_plan_adversarial_steps_contain_continuation_protocol(make_plan_text: str) -> None:
    """Each adversarial step in make-plan must include SendMessage continuation protocol."""
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    step8_idx = make_plan_text.find("**Registry Trace", planning_idx)
    step9_idx = make_plan_text.find("**Plan Revision", planning_idx)
    assert all(i != -1 for i in (step6_idx, step7_idx, step8_idx, step9_idx))

    for label, start, end in [
        ("Step 6 (Foundation Audit)", step6_idx, step7_idx),
        ("Step 7 (Interface Mapping)", step7_idx, step8_idx),
        ("Step 8 (Registry Trace)", step8_idx, step9_idx),
    ]:
        section = make_plan_text[start:end].lower()
        assert "sendmessage" in section, f"{label} must mention SendMessage"
        assert "summary" in section, f"{label} must mention required summary field"
        assert "continuation" in section, f"{label} must mention continuation"


def test_rectify_adversarial_steps_contain_continuation_protocol(rectify_text: str) -> None:
    """Each adversarial step in rectify must include SendMessage continuation protocol."""
    workflow_idx = rectify_text.find("## Rectify Workflow")
    assert workflow_idx != -1
    fa_idx = rectify_text.find("Foundation Audit", workflow_idx)
    im_idx = rectify_text.find("Interface Mapping", workflow_idx)
    rt_idx = rectify_text.find("Registry Trace", workflow_idx)
    assert all(i != -1 for i in (fa_idx, im_idx, rt_idx))

    next_heading = rectify_text.find("\n## ", rt_idx)
    rt_end = next_heading if next_heading != -1 else len(rectify_text)

    for label, start, end in [
        ("Step 5 (Foundation Audit)", fa_idx, im_idx),
        ("Step 6 (Interface Mapping)", im_idx, rt_idx),
        ("Step 7 (Registry Trace)", rt_idx, rt_end),
    ]:
        section = rectify_text[start:end].lower()
        assert "sendmessage" in section, f"{label} must mention SendMessage"
        assert "summary" in section, f"{label} must mention required summary field"
        assert "continuation" in section, f"{label} must mention continuation"
