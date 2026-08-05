"""Phase D contracts for backend-native exploration dispatch rendering."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

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
    pkg_root,
)
from autoskillit.core.io import load_yaml
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

_ARCHITECTURE_LENS_SKILLS = (
    "arch-lens-c4-container",
    "arch-lens-concurrency",
    "arch-lens-data-lineage",
    "arch-lens-deployment",
    "arch-lens-development",
    "arch-lens-error-resilience",
    "arch-lens-module-dependency",
    "arch-lens-operational",
    "arch-lens-process-flow",
    "arch-lens-repository-access",
    "arch-lens-scenarios",
    "arch-lens-security",
    "arch-lens-state-lifecycle",
)


def _architecture_vectors_from_skill(
    skill_name: str,
) -> tuple[ExplorationVectorDef, ...]:
    path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    frontmatter = cast(dict[str, Any], load_yaml(content.split("---", 2)[1]))
    rows = cast(list[dict[str, Any]], frontmatter["exploration_vectors"])
    vectors: list[ExplorationVectorDef] = []

    for row in rows:
        vector_id = cast(str, row["id"])
        profile = RepositoryProfileId(cast(str, row["profile"]))
        marker = f'<!-- autoskillit:exploration-vector id="{vector_id}" -->'
        body = content.split(marker, 1)[1].split("<!-- /autoskillit:exploration-vector -->", 1)[0]
        vectors.append(
            ExplorationVectorDef(
                id=vector_id,
                disposition=ExplorationVectorDisposition(cast(str, row["disposition"])),
                rationale=cast(str, row["rationale"]),
                applicability=ExplorationVectorApplicabilityId(cast(str, row["applicability"])),
                role=cast(str | None, row["role"]),
                profile=profile,
                relationship_classes=tuple(
                    RelationshipKind(value)
                    for value in cast(list[str], row["relationship_classes"])
                ),
                task=ExplorationTaskSpec(
                    task_id=cast(str, row["task_id"]),
                    frontier_item_id=cast(str, row["frontier_item_id"]),
                    profile=profile,
                    depends_on=tuple(cast(list[str], row["depends_on"])),
                    scope=tuple(cast(list[str], row["scope"])),
                ),
                max_results=cast(int, row["max_results"]),
                max_report_bytes=cast(int, row["max_report_bytes"]),
                evidence_version=cast(int, row["evidence_version"]),
                native_dispatch=cast(bool, row["native_dispatch"]),
                body=body,
            )
        )

    return tuple(vectors)


def _actual_architecture_vectors(skill_name: str) -> tuple[ExplorationVectorDef, ...]:
    vectors = _architecture_vectors_from_skill(skill_name)
    assert all(vector.profile is RepositoryProfileId.AUTO for vector in vectors)
    return tuple(
        replace(
            vector,
            profile=RepositoryProfileId.AUTOSKILLIT,
            task=replace(vector.task, profile=RepositoryProfileId.AUTOSKILLIT),
        )
        for vector in vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED and vector.native_dispatch
    )


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


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    [
        (ClaudeCodeBackend(), 'Agent(subagent_type="autoskillit:'),
        (CodexBackend(), 'spawn_agent(agent_type="'),
    ],
)
def test_all_actual_architecture_vectors_render_each_backend_native_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    filesystem_skills = tuple(
        path.parent.name
        for path in sorted((pkg_root() / "skills_extended").glob("arch-lens-*/SKILL.md"))
    )
    assert filesystem_skills == _ARCHITECTURE_LENS_SKILLS

    rendered_count = 0
    for skill_name in _ARCHITECTURE_LENS_SKILLS:
        vectors = _actual_architecture_vectors(skill_name)
        assert vectors, skill_name
        plan = _plan(vectors)
        rendered = backend.exploration_dispatch_renderer.render(plan, vectors)

        assert rendered.router_plan_digest == plan.digest
        assert tuple(rendered.replacements) == tuple(
            vector.id for vector in sorted(vectors, key=lambda item: item.task.task_id)
        )
        for vector in vectors:
            rendered_count += 1
            replacement = rendered.replacements[vector.id]
            assert f'{native_prefix}{vector.role}"' in replacement
            assert f"task_id: {vector.task.task_id}" in replacement
            assert "profile: autoskillit" in replacement
            assert json.dumps(vector.body)[1:-1] in replacement

    assert rendered_count > len(_ARCHITECTURE_LENS_SKILLS)


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
