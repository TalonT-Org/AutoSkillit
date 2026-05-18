"""Contract tests verifying adversarial review pass (Steps 6-8) exists in make-plan SKILL.md."""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def make_plan_text() -> str:
    p = pkg_root() / "skills_extended" / "make-plan" / "SKILL.md"
    return p.read_text()


def test_make_plan_step6_adversarial_review_exists(make_plan_text: str) -> None:
    planning_idx = make_plan_text.find("## Planning Steps")
    assert planning_idx != -1
    step6_idx = make_plan_text.find("**Adversarial Agent Review", planning_idx)
    step7_idx = make_plan_text.find("**Registry Wire Trace", planning_idx)
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "contract verifier" in step6_section, "Step 6 must include Agent A — Contract Verifier"
    assert "completeness auditor" in step6_section, (
        "Step 6 must include Agent B — Completeness Auditor"
    )
    assert "assumption challenger" in step6_section, (
        "Step 6 must include Agent C — Assumption Challenger"
    )


def test_make_plan_step6_agents_are_parallel(make_plan_text: str) -> None:
    step6_idx = make_plan_text.find("**Adversarial Agent Review")
    step7_idx = make_plan_text.find("**Registry Wire Trace")
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "parallel" in step6_section, (
        "Step 6 must describe the 3 adversarial agents as running in parallel"
    )


def test_make_plan_step6_no_scope_expansion(make_plan_text: str) -> None:
    step6_idx = make_plan_text.find("**Adversarial Agent Review")
    step7_idx = make_plan_text.find("**Registry Wire Trace")
    assert step6_idx != -1 and step7_idx != -1
    step6_section = make_plan_text[step6_idx:step7_idx].lower()
    assert "scope expansion" in step6_section or (
        "scope" in step6_section and "not" in step6_section
    ), "Step 6 must restrict agents from suggesting scope expansion"


def test_make_plan_step7_registry_wire_trace_exists(make_plan_text: str) -> None:
    step7_idx = make_plan_text.find("**Registry Wire Trace")
    assert step7_idx != -1, "Step 7 must exist with 'Registry Wire Trace' heading"
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
    assert found >= 6, f"Step 7 must contain at least 6 of 8 sync pattern names, found {found}"


def test_make_plan_step8_plan_revision_exists(make_plan_text: str) -> None:
    step8_idx = make_plan_text.find("**Plan Revision")
    assert step8_idx != -1, "Step 8 must exist with 'Plan Revision' heading"
    next_heading = make_plan_text.find("\n##", step8_idx)
    end = next_heading if next_heading != -1 else len(make_plan_text)
    step8_section = make_plan_text[step8_idx:end].lower()
    assert "revis" in step8_section, "Step 8 must contain revision-related language"
    assert ("four" in step8_section or "4" in step8_section) and (
        "report" in step8_section or "finding" in step8_section
    ), "Step 8 must reference reading the four adversarial reports"


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
    step6_idx = make_plan_text.find("**Adversarial Agent Review")
    step7_idx = make_plan_text.find("**Registry Wire Trace")
    assert step6_idx != -1 and step7_idx != -1
    assert step7_idx > step6_idx, "Step 7 must appear after Step 6 in the document"


def test_make_plan_adversarial_agents_downstream_consumer_instruction(
    make_plan_text: str,
) -> None:
    step6_idx = make_plan_text.find("**Adversarial Agent Review")
    step7_idx = make_plan_text.find("**Registry Wire Trace")
    assert step6_idx != -1 and step7_idx != -1
    agent_a_start = make_plan_text.find("Contract Verifier", step6_idx)
    assert agent_a_start != -1
    agent_a_section = make_plan_text[agent_a_start:step7_idx].lower()
    assert "downstream consumer" in agent_a_section or "consumer" in agent_a_section, (
        "Agent A (Contract Verifier) must instruct tracing downstream consumers"
    )
