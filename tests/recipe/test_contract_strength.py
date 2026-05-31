"""Contract strength validation — structural guards against weak contracts."""

from __future__ import annotations

import regex as re

from autoskillit.recipe._contracts_manifest import get_skill_contract, load_bundled_manifest


def test_write_always_skills_with_completion_required_have_marker_pattern():
    manifest = load_bundled_manifest()
    skills = manifest["skills"]
    failures = []
    for skill_name, skill_data in skills.items():
        if skill_data.get("write_behavior") != "always":
            continue
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
        f"write_behavior='always' + completion_required skills missing "
        f"ORDER_UP pattern: {failures}"
    )


def test_no_sole_optional_capture_group_pattern():
    manifest = load_bundled_manifest()
    skills = manifest["skills"]
    failures = []
    for skill_name, skill_data in skills.items():
        patterns = skill_data.get("expected_output_patterns", [])
        if len(patterns) != 1:
            continue
        for pattern in patterns:
            if pattern.endswith(")?"):
                failures.append(f"{skill_name}: {pattern!r}")
    assert not failures, (
        f"Skills with a single pattern ending in ')?' make the entire "
        f"contract vacuously satisfiable: {failures}"
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
