"""Reachability guard for closed exploration vector applicability identifiers.

Every :class:`ExplorationVectorApplicabilityId` member must either be
producible by ``_resolve_exploration_applicabilities`` from a real caller
scenario, or be explicitly deferred in ``AUTHORING_RESERVED_EXPLORATION_APPLICABILITIES``
with a tracking-issue citation. This guards against a future enum member
being added without wiring its activation predicate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    AUTHORING_RESERVED_EXPLORATION_APPLICABILITIES,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    RelationshipKind,
    RepositoryProfileId,
)
from autoskillit.server._explorer_projection import _resolve_exploration_applicabilities

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _resolve_always_scenario() -> frozenset[ExplorationVectorApplicabilityId]:
    """ALWAYS is the resolver's unconditional seed member."""
    context = SimpleNamespace(exploration_vectors={})
    return _resolve_exploration_applicabilities(context, skill_inputs=None, output_dir="")


def _resolve_planner_extract_domain_deep_scenario(
    tmp_path: Path,
) -> frozenset[ExplorationVectorApplicabilityId]:
    """PLANNER_EXTRACT_DOMAIN_DEEP activates from a bounded analysis.json over a
    module-count threshold, gated behind a migrated vector carrying it."""
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(
        json.dumps({"module_count": 25, "architecture_style": "monolith"}),
        encoding="utf-8",
    )
    vector = ExplorationVectorDef(
        id="deep-domain-vector",
        disposition=ExplorationVectorDisposition.MIGRATED,
        rationale="Extract deep domain knowledge for large repositories.",
        applicability=ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP,
        role="domain-knowledge-extractor",
        profile=RepositoryProfileId.GENERIC_PYTHON,
        relationship_classes=(RelationshipKind.REFERENCES,),
        task=ExplorationTaskSpec(
            "deep-domain-task",
            "deep-domain-frontier",
            RepositoryProfileId.GENERIC_PYTHON,
            scope=("src",),
        ),
        body="Extract deep domain knowledge.",
    )
    context = SimpleNamespace(exploration_vectors={"planner": (vector,)})
    return _resolve_exploration_applicabilities(
        context,
        skill_inputs={"analysis_path": str(analysis_path)},
        output_dir=str(tmp_path),
    )


def _scenario_map(
    tmp_path: Path,
) -> dict[ExplorationVectorApplicabilityId, frozenset[ExplorationVectorApplicabilityId]]:
    """Declarative member -> resolved-active-set map, one real scenario per member.

    This is the single source of truth shared by the producibility and
    completeness tests below: both derive their member coverage from this
    map's keys rather than a second hardcoded set.
    """
    return {
        ExplorationVectorApplicabilityId.ALWAYS: _resolve_always_scenario(),
        ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP: (
            _resolve_planner_extract_domain_deep_scenario(tmp_path)
        ),
    }


def test_every_applicability_is_producible_or_reserved(tmp_path: Path) -> None:
    scenarios = _scenario_map(tmp_path)
    for member, active in scenarios.items():
        assert member in active, (
            f"{member} has a scenario but _resolve_exploration_applicabilities never activated it"
        )


def test_every_enum_member_is_covered_by_a_scenario_or_reserved(tmp_path: Path) -> None:
    scenarios = _scenario_map(tmp_path)
    assert set(ExplorationVectorApplicabilityId) == (
        scenarios.keys() | set(AUTHORING_RESERVED_EXPLORATION_APPLICABILITIES)
    )


def test_reserved_registry_entries_cite_a_tracking_issue() -> None:
    for member, tracking_issue in AUTHORING_RESERVED_EXPLORATION_APPLICABILITIES.items():
        assert re.search(r"#\d+", tracking_issue), (
            f"{member} reserved entry must cite a tracking issue (#NNNN): {tracking_issue!r}"
        )
