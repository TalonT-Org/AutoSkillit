"""Phase D contracts for backend-native exploration dispatch rendering."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    ProfileActivation,
    RelationshipKind,
    RepositoryProfileId,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.feature("exploration"),
    pytest.mark.small,
]


def _vector(
    vector_id: str,
    *,
    role: str,
    applicability: ExplorationVectorApplicabilityId,
    relationships: tuple[RelationshipKind, ...],
) -> ExplorationVectorDef:
    task_id = f"{vector_id}-task"
    return ExplorationVectorDef(
        id=vector_id,
        disposition=ExplorationVectorDisposition.MIGRATED,
        rationale="Use bounded typed evidence while the parent retains synthesis.",
        applicability=applicability,
        role=role,
        profile=RepositoryProfileId.AUTOSKILLIT,
        relationship_classes=relationships,
        task=ExplorationTaskSpec(
            task_id,
            f"{vector_id}-frontier",
            RepositoryProfileId.AUTOSKILLIT,
            scope=(".",),
        ),
        max_results=100,
        max_report_bytes=20_000,
        evidence_version=1,
        native_dispatch=True,
        body=(
            "Return bounded typed evidence only; do not diagnose the root cause, "
            "rank candidates, or select a fix."
        ),
    )


def _plan(vectors: tuple[ExplorationVectorDef, ...]) -> ExplorationRouterPlan:
    ordered = tuple(sorted(vectors, key=lambda vector: vector.task.task_id))
    return ExplorationRouterPlan(
        snapshot=None,
        tasks=tuple(vector.task for vector in ordered),
        activations=(
            ProfileActivation(
                RepositoryProfileId.AUTOSKILLIT,
                ExplorationApplicability.APPLICABLE,
                "trusted Phase D test profile",
            ),
        ),
    )


_STANDARD_NAVIGATOR = _vector(
    "phase-d-standard-navigation",
    role="semantic-code-navigator",
    applicability=ExplorationVectorApplicabilityId.INVESTIGATE_STANDARD,
    relationships=(RelationshipKind.DEFINES, RelationshipKind.CALLS),
)
_DEEP_PROFILER = _vector(
    "phase-d-deep-impact",
    role="repository-impact-profiler",
    applicability=ExplorationVectorApplicabilityId.INVESTIGATE_DEEP,
    relationships=(RelationshipKind.REFERENCES, RelationshipKind.AFFECTS),
)
_VECTORS = (_STANDARD_NAVIGATOR, _DEEP_PROFILER)


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    [
        (ClaudeCodeBackend(), 'Agent(subagent_type="autoskillit:'),
        (CodexBackend(), 'spawn_agent(agent_type="'),
    ],
)
def test_phase_d_neutral_plan_renders_each_backend_native_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    plan = _plan(_VECTORS)

    rendered = backend.exploration_dispatch_renderer.render(plan, _VECTORS)

    assert rendered.router_plan_digest == plan.digest
    assert set(rendered.replacements) == {vector.id for vector in _VECTORS}
    for vector in _VECTORS:
        replacement = rendered.replacements[vector.id]
        assert native_prefix in replacement
        assert f"task_id: {vector.task.task_id}" in replacement
        assert "profile: autoskillit" in replacement
        assert (
            "Submit this typed task packet to the deterministic exploration router" in replacement
        )
        assert "Return bounded typed evidence only" in replacement


def test_phase_d_backends_preserve_neutral_plan_and_role_identities() -> None:
    plan = _plan(_VECTORS)

    claude = ClaudeCodeBackend().exploration_dispatch_renderer.render(plan, _VECTORS)
    codex = CodexBackend().exploration_dispatch_renderer.render(plan, _VECTORS)

    assert claude.router_plan_digest == codex.router_plan_digest == plan.digest
    assert claude.role_definition_digests == codex.role_definition_digests
    assert claude.replacements != codex.replacements


@pytest.mark.parametrize("selected", _VECTORS, ids=lambda vector: vector.id)
def test_branch_selection_builds_a_plan_only_for_the_selected_vector(
    selected: ExplorationVectorDef,
) -> None:
    plan = _plan((selected,))

    rendered = CodexBackend().exploration_dispatch_renderer.render(plan, (selected,))

    assert plan.tasks == (selected.task,)
    assert set(rendered.replacements) == {selected.id}
    assert selected.applicability in {
        ExplorationVectorApplicabilityId.INVESTIGATE_STANDARD,
        ExplorationVectorApplicabilityId.INVESTIGATE_DEEP,
    }
