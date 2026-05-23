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
    assert "scope expansion" in step6_section or (
        "scope" in step6_section and "not" in step6_section
    ), "Step 6 must restrict agents from suggesting scope expansion"
    assert "junior reviewer" in step6_section, "Step 6 must include contrastive prompt frame"


def test_make_plan_step6_contrastive_frame_exists(make_plan_text: str) -> None:
    """The new design is sequential (one agent per step), not parallel."""
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "junior reviewer" in step6_section, (
        "Step 6 must include the contrastive prompt frame 'junior reviewer'"
    )


def test_make_plan_step6_no_scope_expansion(make_plan_text: str) -> None:
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Interface Mapping", planning_idx)
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "scope expansion" in step6_section or (
        "scope" in step6_section and "not" in step6_section
    ), "Step 6 must restrict agents from suggesting scope expansion"


def test_make_plan_step7_registry_wire_trace_exists(make_plan_text: str) -> None:
    step7_idx = make_plan_text.find("**Registry Trace")
    assert step7_idx != -1, "Step 7 must exist with 'Registry Trace' heading"
    step8_idx = make_plan_text.find("**Plan Revision", step7_idx)
    assert step8_idx != -1
    step7_section = make_plan_text[step7_idx:step8_idx].upper()
    sync_patterns = [
        "RETIRED",
        "RE-EXPORT",
        "RULE REGISTRATION",
        "IMPORT LAYER",
        "TYPED ALIASES",
        "DERIVED ARTIFACTS",
    ]
    found = sum(1 for p in sync_patterns if p in step7_section)
    gated_tools = "GATED_TOOLS" in step7_section or "TOOL REGISTRIES" in step7_section
    dual_copy = "DUAL-COPY" in step7_section or "SKILL_FILE_ADVISORY_MAP" in step7_section
    found += int(gated_tools) + int(dual_copy)
    assert found >= 7, f"Step 7 must contain at least 7 of 8 sync pattern names, found {found}"


def test_make_plan_step8_plan_revision_exists(make_plan_text: str) -> None:
    step8_idx = make_plan_text.find("**Plan Revision")
    assert step8_idx != -1, "Step 8 must exist with 'Plan Revision' heading"
    next_heading = make_plan_text.find("\n##", step8_idx)
    end = next_heading if next_heading != -1 else len(make_plan_text)
    step8_section = make_plan_text[step8_idx:end].lower()
    assert "revis" in step8_section, "Step 8 must contain revision-related language"
    assert ("three" in step8_section or "3" in step8_section) and (
        "report" in step8_section or "finding" in step8_section
    ), "Step 8 must reference reading the three adversarial reports"


def test_make_plan_checklist_includes_adversarial_review(make_plan_text: str) -> None:
    checklist_idx = make_plan_text.find("## Skill Loading Checklist")
    assert checklist_idx != -1
    next_heading = make_plan_text.find("\n## ", checklist_idx + 1)
    end = next_heading if next_heading != -1 else len(make_plan_text)
    checklist_section = make_plan_text[checklist_idx:end].lower()
    assert "adversarial" in checklist_section, (
        "Skill Loading Checklist must include an adversarial review checklist item"
    )


def test_make_plan_step7_runs_after_step6(make_plan_text: str) -> None:
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Foundation Audit", planning_idx)
    step7_idx = make_plan_text.find("**Registry Trace", planning_idx)
    assert step6_idx != -1 and step7_idx != -1
    assert step7_idx > step6_idx, "Step 7 must appear after Step 6 in the document"


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

    # Step 8 (Registry Trace) must contain registry/symbol responsibility
    step9_idx = make_plan_text.find("**Plan Revision", step8_idx)
    assert step9_idx != -1
    step8_section = make_plan_text[step8_idx:step9_idx].lower()
    assert "registry" in step8_section or "symbol" in step8_section, (
        "Step 8 (Registry Trace) must mention registry/symbol tracing"
    )
