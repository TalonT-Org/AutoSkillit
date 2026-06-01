"""Contract strength validation — structural guards against weak contracts."""

from __future__ import annotations

import pytest
import regex as re

from autoskillit.recipe._contracts_manifest import get_skill_contract, load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


_KNOWN_UNGAPPED_WRITE_ALWAYS = frozenset(
    {
        "audit-tests",
        "build-execution-map",
        "compose-research-pr",
        "design-guards",
        "diagnose-ci",
        "download-data",
        "dry-walkthrough",
        "file-audit-issues",
        "generate-report",
        "make-campaign",
        "make-groups",
        "plan-experiment",
        "plan-visualization",
        "planner-assess-review-approach",
        "planner-consolidate-wps",
        "planner-elaborate-assignments",
        "planner-elaborate-phase",
        "planner-elaborate-wps",
        "planner-generate-phases",
        "planner-refine",
        "planner-refine-assignments",
        "planner-refine-phases",
        "planner-refine-wps",
        "planner-validate-task-alignment",
        "prepare-research-pr",
        "rectify",
        "report-bug",
        "resolve-design-review",
        "review-design",
        "run-experiment",
        "scope",
        "select-directions",
        "select-vis-lenses",
        "setup-environment",
        "stage-data",
        "synthesize-vis-plan",
        "troubleshoot-experiment",
        "write-recipe",
    }
)


def test_write_always_skills_require_completion_marker_pattern():
    manifest = load_bundled_manifest()
    skills = manifest["skills"]
    ungapped: set[str] = set()
    no_contract: list[str] = []
    for skill_name, skill_data in skills.items():
        if skill_data.get("write_behavior") != "always":
            continue
        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            no_contract.append(skill_name)
            continue
        has_marker_pattern = any(
            re.search(pattern, "%%ORDER_UP::abcdef12%%")
            for pattern in contract.expected_output_patterns
        )
        has_completion_required = skill_data.get("completion_required", False)
        if not has_marker_pattern and not has_completion_required:
            ungapped.add(skill_name)
    assert not no_contract, f"contract not found: {no_contract}"
    new_ungapped = ungapped - _KNOWN_UNGAPPED_WRITE_ALWAYS
    assert not new_ungapped, (
        f"New write_behavior='always' skills missing both ORDER_UP pattern "
        f"and completion_required — add a guard or update "
        f"_KNOWN_UNGAPPED_WRITE_ALWAYS: {sorted(new_ungapped)}"
    )
    stale = _KNOWN_UNGAPPED_WRITE_ALWAYS - ungapped
    assert not stale, (
        f"Skills in _KNOWN_UNGAPPED_WRITE_ALWAYS now have a guard — "
        f"remove from allowlist: {sorted(stale)}"
    )


_KNOWN_OPTIONAL_CAPTURE_EXEMPTIONS = frozenset(
    {
        "promote-to-main",
    }
)


def test_no_optional_capture_groups_in_required_patterns():
    manifest = load_bundled_manifest()
    skills = manifest["skills"]
    failures = []
    exempted: set[str] = set()
    for skill_name, skill_data in skills.items():
        patterns = skill_data.get("expected_output_patterns", [])
        for pattern in patterns:
            if pattern.endswith(")?"):
                if skill_name in _KNOWN_OPTIONAL_CAPTURE_EXEMPTIONS:
                    exempted.add(skill_name)
                else:
                    failures.append(f"{skill_name}: {pattern!r}")
    assert not failures, (
        f"Patterns ending in ')?' make the captured value optional, "
        f"weakening the contract: {failures}"
    )
    stale = _KNOWN_OPTIONAL_CAPTURE_EXEMPTIONS - exempted
    assert not stale, (
        f"Skills in _KNOWN_OPTIONAL_CAPTURE_EXEMPTIONS no longer have "
        f"optional patterns — remove from exemption set: {sorted(stale)}"
    )


def test_completion_required_skills_have_marker_pattern():
    manifest = load_bundled_manifest()
    skills = manifest["skills"]
    failures = []
    for skill_name, skill_data in skills.items():
        if not skill_data.get("completion_required", False):
            continue
        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            failures.append(f"{skill_name}: contract not found")
            continue
        has_marker_pattern = any(
            re.search(pattern, "%%ORDER_UP::abcdef12%%")
            for pattern in contract.expected_output_patterns
        )
        if not has_marker_pattern:
            failures.append(skill_name)
    assert not failures, (
        f"completion_required skills missing a pattern matching %%ORDER_UP::abcdef12%%: {failures}"
    )
