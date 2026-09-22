"""Direct unit coverage for the project-local contract-floor weakening layer."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _skill_info(
    tmp_path: Path,
    *,
    name: str,
    semantic_plan,
    required_resources: tuple[str, ...] = (),
):
    """Build a ``SkillInfo`` with controlled semantic_plan + resource surface."""
    from autoskillit.core import SkillSource
    from autoskillit.workspace.skills import SkillInfo

    return SkillInfo(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=tmp_path / f"{name}.md",
        semantic_plan=semantic_plan,
        required_resources=required_resources,
    )


def test_contract_floor_returns_empty_when_bundled_is_missing(tmp_path: Path) -> None:
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    candidate = _skill_info(tmp_path, name="x", semantic_plan=None)

    assert contract_floor_invalidities(candidate, bundled=None) == ()


def test_contract_floor_returns_empty_when_bundled_plan_is_none(tmp_path: Path) -> None:
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    candidate = _skill_info(tmp_path, name="x", semantic_plan=None)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=None)

    assert contract_floor_invalidities(candidate, bundled) == ()


def test_contract_floor_flags_semantic_requirements_dropped(tmp_path: Path) -> None:
    from autoskillit.core import SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(schema_version=1)
    candidate = _skill_info(tmp_path, name="x", semantic_plan=None)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    invalidity = invalidities[0]
    assert invalidity.kind.value == "contract_floor_weakened"
    assert "semantic_requirements" in invalidity.detail


def test_contract_floor_flags_join_required_weakening(tmp_path: Path) -> None:
    from autoskillit.core import JoinSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    candidate_plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=False))
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    assert "join.required" in invalidities[0].detail


def test_contract_floor_flags_concurrency_required_weakening(tmp_path: Path) -> None:
    from autoskillit.core import ConcurrencySpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(schema_version=1, concurrency=ConcurrencySpec(required=True))
    candidate_plan = SkillSemanticPlan(
        schema_version=1, concurrency=ConcurrencySpec(required=False)
    )
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    assert "concurrency.required" in invalidities[0].detail


def test_contract_floor_flags_evidence_required_weakening(tmp_path: Path) -> None:
    from autoskillit.core import EvidenceSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(
        schema_version=1, evidence=EvidenceSpec(required=True, independent=True)
    )
    candidate_plan = SkillSemanticPlan(
        schema_version=1, evidence=EvidenceSpec(required=False, independent=False)
    )
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    assert "evidence.required" in invalidities[0].detail


def test_contract_floor_flags_evidence_independent_weakening(tmp_path: Path) -> None:
    from autoskillit.core import EvidenceSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(
        schema_version=1, evidence=EvidenceSpec(required=True, independent=True)
    )
    candidate_plan = SkillSemanticPlan(
        schema_version=1, evidence=EvidenceSpec(required=True, independent=False)
    )
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    assert "evidence.independent" in invalidities[0].detail


def test_contract_floor_aggregates_multiple_weakenings(tmp_path: Path) -> None:
    from autoskillit.core import ConcurrencySpec, EvidenceSpec, JoinSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(
        schema_version=1,
        join=JoinSpec(required=True),
        concurrency=ConcurrencySpec(required=True),
        evidence=EvidenceSpec(required=True, independent=True),
    )
    candidate_plan = SkillSemanticPlan(schema_version=1)
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    detail = invalidities[0].detail
    assert "join.required" in detail
    assert "concurrency.required" in detail
    assert "evidence.required" in detail
    assert "evidence.independent" in detail


def test_contract_floor_returns_empty_for_strict_or_matching_override(tmp_path: Path) -> None:
    from autoskillit.core import ConcurrencySpec, JoinSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(
        schema_version=1,
        join=JoinSpec(required=True),
        concurrency=ConcurrencySpec(required=True),
    )
    candidate_plan = SkillSemanticPlan(
        schema_version=1,
        join=JoinSpec(required=True),
        concurrency=ConcurrencySpec(required=True),
    )
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    assert contract_floor_invalidities(candidate, bundled) == ()


def test_contract_floor_detail_includes_dropped_resources(tmp_path: Path) -> None:
    from autoskillit.core import JoinSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    candidate_plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=False))
    candidate = _skill_info(
        tmp_path, name="x", semantic_plan=candidate_plan, required_resources=()
    )
    bundled = _skill_info(
        tmp_path,
        name="x",
        semantic_plan=bundled_plan,
        required_resources=("git-write", "internet"),
    )

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    detail = invalidities[0].detail
    assert "join.required" in detail
    assert "git-write" in detail
    assert "internet" in detail
    assert "dropped requires_resources" in detail


def test_contract_floor_detail_includes_dropped_git_metadata_writes(tmp_path: Path) -> None:
    from autoskillit.core import GitMetadataWriteSpec, JoinSpec, SkillSemanticPlan
    from autoskillit.workspace.skills._contract_floor import contract_floor_invalidities

    bundled_plan = SkillSemanticPlan(
        schema_version=1,
        join=JoinSpec(required=True),
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create-commit"),),
    )
    candidate_plan = SkillSemanticPlan(
        schema_version=1, join=JoinSpec(required=False), git_metadata_writes=()
    )
    candidate = _skill_info(tmp_path, name="x", semantic_plan=candidate_plan)
    bundled = _skill_info(tmp_path, name="x", semantic_plan=bundled_plan)

    invalidities = contract_floor_invalidities(candidate, bundled)

    assert len(invalidities) == 1
    detail = invalidities[0].detail
    assert "join.required" in detail
    assert "dropped git_metadata_writes" in detail
    assert "create-commit" in detail
