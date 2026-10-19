"""Planner and audit prose stays aligned with stage-owned plan obligations."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_STAGE_KEY_DEFINITION = re.compile(r"(?m)^[ \t]*-\s+`([^`]+)`\s*(?:—|:)\s+\S.*$")
_NEVER_PUBLISH = (
    "Never open, merge, or close pull requests or issues — publication belongs to the "
    "recipe's later stages."
)


def test_plan_part_form_lists_every_stage_obligation_key() -> None:
    from autoskillit.core import StageObligation

    form = (pkg_root() / "skill_resources" / "plan-part-form.md").read_text(encoding="utf-8")
    listed_keys = set(_STAGE_KEY_DEFINITION.findall(form))

    assert listed_keys == {obligation.value for obligation in StageObligation}


def test_planner_skills_document_stage_allocation_and_echo_exemption() -> None:
    skills_root = pkg_root() / "skills_extended"
    for skill_name in ("make-plan", "rectify"):
        content = " ".join(
            (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8").split()
        )
        assert "opening the pull request, or leaving it unmerged" in content
        assert "allocated as `stage` with its obligation key" in content
        assert "never to an implementation step; bind refuses one this run cannot perform" in (
            content
        )
        assert "Stage-owned outcomes are exempt from echoing into steps." in content
        assert "They appear in the plan only as their allocation rows" in content


def test_audit_skills_limit_stage_exclusions_to_authenticated_evidence() -> None:
    package_root = pkg_root()
    audit_skill = (package_root / "skills_extended" / "audit-impl" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    audit_skill = " ".join(audit_skill.split())
    slice_auditor = (package_root / "agents" / "audit-impl-slice-auditor.md").read_text(
        encoding="utf-8"
    )

    assert "verified_plan_set_preflight.stage_obligations" in audit_skill
    assert "Allocation prose alone does not authorize an exclusion." in audit_skill
    assert "Exclude stage-only requirements" in audit_skill
    assert "retain the code obligation" in audit_skill
    assert "When no authenticated stage evidence is supplied" in audit_skill
    assert "Obligations explicitly owned by a recipe stage in authenticated preflight" in (
        slice_auditor
    )
    assert "Retain every code obligation" in slice_auditor


def test_implementer_skills_leave_publication_to_recipe_stages() -> None:
    skills_root = pkg_root() / "skills_extended"
    for skill_name in ("implement-worktree", "implement-worktree-no-merge", "retry-worktree"):
        content = (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert _NEVER_PUBLISH in content
