"""Trusted profile and closed applicability authority for explorer projection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    ExplorationVectorApplicabilityId,
    ExplorationVectorDisposition,
    RepositoryProfileId,
    SkillContractError,
    SkillSource,
    pkg_root,
)
from autoskillit.server._explorer_projection import (
    _resolve_exploration_applicabilities,
    _resolve_exploration_profile,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter

# Applicability IDs removed from ExplorationVectorApplicabilityId by the
# exploration-vector sidecar migration (SKILL_PROJECTION_VERSION bump to 6).
_REMOVED_EXPLORATION_APPLICABILITY_IDS = frozenset(
    {"investigate-standard", "investigate-deep", "scope-software", "scope-non-software"}
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _projection(
    *,
    project_root,
    profile=RepositoryProfileId.AUTO,
    applicability=None,
    disposition=ExplorationVectorDisposition.MIGRATED,
):
    vector = SimpleNamespace(
        profile=profile,
        applicability=applicability or ExplorationVectorApplicabilityId.ALWAYS,
        disposition=disposition,
    )
    return SimpleNamespace(
        project_root=project_root,
        exploration_vectors={"planner": (vector,)},
    )


def test_profile_auto_uses_only_factory_trusted_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "autoskillit.server._explorer_projection.resolve_repository_profile",
        lambda root: RepositoryProfileId.AUTOSKILLIT,
    )
    tool_ctx = SimpleNamespace(exploration_context_store=SimpleNamespace(trusted_root=tmp_path))

    assert (
        _resolve_exploration_profile(
            tool_ctx,
            _projection(project_root=tmp_path),
            active_applicabilities=frozenset({ExplorationVectorApplicabilityId.ALWAYS}),
        )
        is RepositoryProfileId.AUTOSKILLIT
    )
    with pytest.raises(SkillContractError, match="not the trusted repository root"):
        _resolve_exploration_profile(
            tool_ctx,
            _projection(project_root=tmp_path / "unrelated"),
            active_applicabilities=frozenset({ExplorationVectorApplicabilityId.ALWAYS}),
        )


@pytest.mark.parametrize(
    ("disposition", "applicability"),
    [
        (
            ExplorationVectorDisposition.RETAINED,
            ExplorationVectorApplicabilityId.ALWAYS,
        ),
        (
            ExplorationVectorDisposition.EXCLUDED,
            ExplorationVectorApplicabilityId.ALWAYS,
        ),
        (
            ExplorationVectorDisposition.MIGRATED,
            ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP,
        ),
    ],
)
def test_profile_auto_ignores_vectors_that_cannot_render(
    disposition: ExplorationVectorDisposition,
    applicability: ExplorationVectorApplicabilityId,
) -> None:
    tool_ctx = SimpleNamespace(exploration_context_store=None)

    resolved = _resolve_exploration_profile(
        tool_ctx,
        _projection(
            project_root=None,
            disposition=disposition,
            applicability=applicability,
        ),
        active_applicabilities=frozenset({ExplorationVectorApplicabilityId.ALWAYS}),
    )

    assert resolved is None


@pytest.mark.parametrize(
    "disposition",
    [ExplorationVectorDisposition.RETAINED, ExplorationVectorDisposition.EXCLUDED],
)
def test_non_migrated_applicability_does_not_require_invocation_inputs(
    disposition: ExplorationVectorDisposition,
) -> None:
    active = _resolve_exploration_applicabilities(
        _projection(
            project_root=None,
            disposition=disposition,
            applicability=ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP,
        ),
        skill_inputs=None,
        output_dir="",
    )

    assert active == frozenset({ExplorationVectorApplicabilityId.ALWAYS})


@pytest.mark.parametrize(
    ("module_count", "architecture_style", "deep"),
    [(20, "monolith", False), (21, "monolith", True), (3, "hexagonal", True)],
)
def test_extract_domain_applicability_is_evaluated_from_bounded_analysis(
    tmp_path: Path,
    module_count: int,
    architecture_style: str,
    deep: bool,
) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "module_count": module_count,
                "architecture_style": architecture_style,
            }
        ),
        encoding="utf-8",
    )
    context = _projection(
        project_root=tmp_path,
        applicability=ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP,
    )

    active = _resolve_exploration_applicabilities(
        context,
        skill_inputs={"analysis_path": str(analysis_path)},
        output_dir=str(tmp_path),
    )

    assert ExplorationVectorApplicabilityId.ALWAYS in active
    assert (ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP in active) is deep


def test_investigate_activation_projects_all_migrated_vectors_as_dispatch() -> None:
    path = pkg_root() / "skills_extended" / "investigate" / "SKILL.md"
    info = _skill_info_from_frontmatter("investigate", SkillSource.BUNDLED, path)

    assert not info.invalidities, info.invalidities
    migrated = [
        vector
        for vector in info.exploration_vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    ]
    retained = [
        vector
        for vector in info.exploration_vectors
        if vector.disposition is ExplorationVectorDisposition.RETAINED
    ]

    assert len(migrated) == 15
    assert len(retained) == 14
    assert not any(
        vector.applicability.value in _REMOVED_EXPLORATION_APPLICABILITY_IDS
        for vector in info.exploration_vectors
    )
    assert all(
        vector.applicability is ExplorationVectorApplicabilityId.ALWAYS for vector in migrated
    )
