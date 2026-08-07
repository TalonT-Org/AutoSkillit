"""Neutral router-plan identity and dependency scheduling contracts."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationTaskSpec,
    ProfileActivation,
    RepositoryProfileId,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_authoring_plan_digest_is_backend_neutral_and_snapshot_free() -> None:
    task = ExplorationTaskSpec(
        "semantic",
        "semantic-frontier",
        RepositoryProfileId.AUTOSKILLIT,
        scope=("src",),
    )
    activation = ProfileActivation(
        RepositoryProfileId.AUTOSKILLIT,
        ExplorationApplicability.APPLICABLE,
        "authoring applicability:always",
    )

    first = ExplorationRouterPlan(None, (task,), (activation,))
    second = ExplorationRouterPlan(None, (task,), (activation,))

    assert first.digest == second.digest
    assert first.snapshot is None
