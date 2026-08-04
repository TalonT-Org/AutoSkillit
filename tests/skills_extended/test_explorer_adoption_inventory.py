"""Reviewed inventory for the first planner exploration-vector adopters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from autoskillit.core import ExplorationVectorDisposition, SkillSource, pkg_root
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.small]

InventoryRow = tuple[str, str, str | None, str]

_INVENTORY: dict[str, tuple[InventoryRow, ...]] = {
    "planner-analyze": (
        (
            "languages-frameworks",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-languages-frameworks",
        ),
        (
            "test-infrastructure",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-test-infrastructure",
        ),
        (
            "architecture-patterns",
            "migrated",
            "semantic-code-navigator",
            "planner-analyze-architecture-patterns",
        ),
        (
            "existing-conventions",
            "migrated",
            "semantic-code-navigator",
            "planner-analyze-existing-conventions",
        ),
        (
            "existing-conventions-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-existing-conventions-impact",
        ),
    ),
    "planner-extract-domain": (
        (
            "domain-vocabulary",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-vocabulary",
        ),
        (
            "existing-abstractions",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-abstractions",
        ),
        (
            "integration-points",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-integration-points",
        ),
        (
            "integration-consumers",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-integration-consumers",
        ),
        (
            "cross-cutting-concerns",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-cross-cutting",
        ),
        (
            "data-flow-patterns",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-data-flow",
        ),
        (
            "cross-cutting-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-cross-cutting-impact",
        ),
    ),
    "planner-elaborate-phase": (
        (
            "affected-files",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-affected-files",
        ),
        (
            "affected-file-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-affected-file-impact",
        ),
        (
            "dependency-analysis",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-dependencies",
        ),
        (
            "test-coverage",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-test-coverage",
        ),
        (
            "pattern-discovery",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-patterns",
        ),
        (
            "cross-phase-boundaries",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-boundaries",
        ),
    ),
}


@pytest.fixture
def adoption_inventory() -> Mapping[str, Sequence[InventoryRow]]:
    return _INVENTORY


@pytest.mark.parametrize("skill_name", sorted(_INVENTORY))
def test_planner_skill_vectors_match_reviewed_inventory(
    skill_name: str,
    adoption_inventory: Mapping[str, Sequence[InventoryRow]],
) -> None:
    expected = adoption_inventory[skill_name]
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"

    info = _skill_info_from_frontmatter(
        skill_name,
        SkillSource.BUNDLED_EXTENDED,
        skill_path,
    )

    assert info.invalid_reason is None
    assert [
        [vector.id, vector.disposition.value, vector.role, vector.task.task_id]
        for vector in info.exploration_vectors
    ] == [list(row) for row in expected]
    assert all(vector.profile.value == "auto" for vector in info.exploration_vectors)
    assert all(vector.task.scope == (".",) for vector in info.exploration_vectors)
    assert all(vector.task.depends_on == () for vector in info.exploration_vectors)
    assert all(vector.body.strip() for vector in info.exploration_vectors)

    content = skill_path.read_text(encoding="utf-8")
    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
    assert content.count("<!-- /autoskillit:exploration-vector -->") == len(expected)


def test_dynamic_deep_mode_vectors_have_closed_conditional_applicability() -> None:
    skill_name = "planner-extract-domain"
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(
        skill_name,
        SkillSource.BUNDLED_EXTENDED,
        skill_path,
    )

    conditional = tuple(
        vector
        for vector in info.exploration_vectors
        if vector.applicability.value == "planner-extract-domain-deep"
    )

    assert tuple(vector.id for vector in conditional) == (
        "cross-cutting-concerns",
        "data-flow-patterns",
        "cross-cutting-impact",
    )
    assert all(
        vector.disposition is ExplorationVectorDisposition.MIGRATED and vector.native_dispatch
        for vector in conditional
    )
    assert {vector.role for vector in conditional} == {
        "semantic-code-navigator",
        "repository-impact-profiler",
    }


def test_inventory_is_complete_for_owned_planner_adopters(
    adoption_inventory: Mapping[str, Sequence[InventoryRow]],
) -> None:
    inventory = adoption_inventory

    assert set(inventory) == {
        "planner-analyze",
        "planner-extract-domain",
        "planner-elaborate-phase",
    }
    assert sum(len(vectors) for vectors in inventory.values()) == 18
    assert (
        sum(
            disposition == "migrated"
            for vectors in inventory.values()
            for _, disposition, _, _ in vectors
        )
        == 18
    )
