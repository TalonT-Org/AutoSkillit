"""Trusted profile and closed applicability authority for explorer projection."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    ExplorationVectorApplicabilityId,
    RepositoryProfileId,
    SkillContractError,
)
from autoskillit.server._explorer_projection import (
    _resolve_exploration_applicabilities,
    _resolve_exploration_profile,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _projection(*, project_root, profile=RepositoryProfileId.AUTO, applicability=None):
    vector = SimpleNamespace(
        profile=profile,
        applicability=applicability or ExplorationVectorApplicabilityId.ALWAYS,
    )
    return SimpleNamespace(
        project_root=project_root,
        exploration_vectors={"planner": (vector,)},
    )


def test_profile_auto_uses_only_factory_trusted_repository(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "autoskillit.server._explorer_projection.resolve_repository_profile",
        lambda root: RepositoryProfileId.AUTOSKILLIT,
    )
    tool_ctx = SimpleNamespace(exploration_context_store=SimpleNamespace(trusted_root=tmp_path))

    assert (
        _resolve_exploration_profile(tool_ctx, _projection(project_root=tmp_path))
        is RepositoryProfileId.AUTOSKILLIT
    )
    with pytest.raises(SkillContractError, match="not the trusted repository root"):
        _resolve_exploration_profile(
            tool_ctx,
            _projection(project_root=tmp_path / "unrelated"),
        )


@pytest.mark.parametrize(
    ("module_count", "architecture_style", "deep"),
    [(20, "monolith", False), (21, "monolith", True), (3, "hexagonal", True)],
)
def test_extract_domain_applicability_is_evaluated_from_bounded_analysis(
    tmp_path,
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
