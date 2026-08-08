"""Deterministic explorer router dependency scheduling contracts."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationTaskSpec,
    ProfileActivation,
    RepositoryProfileId,
)
from autoskillit.exploration.router import readiness_waves

pytestmark = [
    pytest.mark.layer("exploration"),
    pytest.mark.feature("exploration"),
    pytest.mark.small,
]


def test_router_keeps_dependency_chain_sequential() -> None:
    tasks = (
        ExplorationTaskSpec(
            "semantic",
            "semantic-frontier",
            RepositoryProfileId.AUTOSKILLIT,
            scope=("src",),
        ),
        ExplorationTaskSpec(
            "impact",
            "impact-frontier",
            RepositoryProfileId.AUTOSKILLIT,
            depends_on=("semantic",),
            scope=("tests",),
        ),
    )
    plan = ExplorationRouterPlan(
        None,
        tasks,
        (
            ProfileActivation(
                RepositoryProfileId.AUTOSKILLIT,
                ExplorationApplicability.APPLICABLE,
                "authoring applicability:always",
            ),
        ),
    )

    assert [wave.items for wave in readiness_waves(plan)] == [("semantic",), ("impact",)]
