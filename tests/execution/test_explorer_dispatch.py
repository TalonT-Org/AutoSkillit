"""Phase D contracts for backend-native exploration dispatch rendering."""

from __future__ import annotations

import json
from dataclasses import replace

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
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.workspace.skills import (
    _bind_exploration_vector_markers,
    _load_exploration_sidecar,
    _parse_exploration_sidecar,
)

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
    applicability=ExplorationVectorApplicabilityId.ALWAYS,
    relationships=(RelationshipKind.DEFINES, RelationshipKind.CALLS),
)
_DEEP_PROFILER = _vector(
    "phase-d-deep-impact",
    role="repository-impact-profiler",
    applicability=ExplorationVectorApplicabilityId.ALWAYS,
    relationships=(RelationshipKind.REFERENCES, RelationshipKind.AFFECTS),
)
_VECTORS = (_STANDARD_NAVIGATOR, _DEEP_PROFILER)

_BACKEND_NATIVE_PREFIX_CASES = (
    pytest.param(
        ClaudeCodeBackend(),
        'Agent(subagent_type="autoskillit:',
        id="claude",
    ),
    pytest.param(
        CodexBackend(),
        'spawn_agent(agent_type="',
        id="codex",
    ),
)

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

_EXPERIMENT_LENS_SKILLS = (
    "exp-lens-estimand-clarity",
    "exp-lens-causal-assumptions",
    "exp-lens-comparator-construction",
    "exp-lens-pipeline-integrity",
    "exp-lens-variance-stability",
    "exp-lens-fair-comparison",
    "exp-lens-reproducibility-artifacts",
    "exp-lens-measurement-validity",
    "exp-lens-sensitivity-robustness",
    "exp-lens-benchmark-representativeness",
    "exp-lens-unit-interference",
    "exp-lens-error-budget",
    "exp-lens-severity-testing",
    "exp-lens-randomization-blocking",
    "exp-lens-validity-threats",
    "exp-lens-iterative-learning",
    "exp-lens-exploratory-confirmatory",
    "exp-lens-governance-risk",
)

_VISUALIZATION_LENS_SKILLS = (
    "vis-lens-always-on",
    "vis-lens-antipattern",
    "vis-lens-caption-annot",
    "vis-lens-chart-select",
    "vis-lens-color-access",
    "vis-lens-figure-table",
    "vis-lens-methodology-norms",
    "vis-lens-multi-compare",
    "vis-lens-reproducibility",
    "vis-lens-story-arc",
    "vis-lens-temporal",
    "vis-lens-uncertainty",
)


def _vectors_from_skill(skill_name: str) -> tuple[ExplorationVectorDef, ...]:
    path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    sidecar_data, _digest = _load_exploration_sidecar(path)
    vectors = _parse_exploration_sidecar(sidecar_data, skill_name)
    return _bind_exploration_vector_markers(content, vectors)


def _actual_skill_vectors(skill_name: str) -> tuple[ExplorationVectorDef, ...]:
    vectors = _vectors_from_skill(skill_name)
    assert all(vector.profile is RepositoryProfileId.AUTO for vector in vectors)
    return tuple(
        replace(
            vector,
            profile=RepositoryProfileId.AUTOSKILLIT,
            task=replace(vector.task, profile=RepositoryProfileId.AUTOSKILLIT),
        )
        for vector in vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    )


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    _BACKEND_NATIVE_PREFIX_CASES,
)
def test_phase_d_neutral_plan_renders_each_backend_native_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    plan = _plan(_VECTORS)

    rendered = backend.exploration_dispatch_renderer.render(plan, _VECTORS)

    assert rendered.router_plan_digest == plan.digest
    assert set(rendered.replacements) == {vector.id for vector in _VECTORS}
    assert (
        "Submit this typed task packet to the deterministic exploration router"
        in rendered.preamble
    )
    for vector in _VECTORS:
        replacement = rendered.replacements[vector.id]
        assert native_prefix in replacement
        assert f"task_id: {vector.task.task_id}" in replacement
        assert "profile: autoskillit" in replacement
        assert (
            "Submit this typed task packet to the deterministic exploration router"
            not in replacement
        )
        assert "Return bounded typed evidence only" in replacement


def test_phase_d_renderer_rejects_unknown_canonical_roles() -> None:
    unknown = replace(_STANDARD_NAVIGATOR, role="unregistered-explorer-role")

    with pytest.raises(ValueError, match="unknown roles.*unregistered-explorer-role"):
        CodexBackend().exploration_dispatch_renderer.render(_plan((unknown,)), (unknown,))


def test_phase_d_backends_preserve_neutral_plan_and_role_identities() -> None:
    plan = _plan(_VECTORS)

    claude = ClaudeCodeBackend().exploration_dispatch_renderer.render(plan, _VECTORS)
    codex = CodexBackend().exploration_dispatch_renderer.render(plan, _VECTORS)

    assert claude.router_plan_digest == codex.router_plan_digest == plan.digest
    assert claude.role_definition_digests == codex.role_definition_digests
    assert claude.replacements != codex.replacements


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    _BACKEND_NATIVE_PREFIX_CASES,
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
        vectors = _actual_skill_vectors(skill_name)
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


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    _BACKEND_NATIVE_PREFIX_CASES,
)
def test_all_actual_experiment_vectors_render_each_backend_native_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    filesystem_skills = {
        path.parent.name for path in (pkg_root() / "skills_extended").glob("exp-lens-*/SKILL.md")
    }
    assert filesystem_skills == set(_EXPERIMENT_LENS_SKILLS)

    rendered_count = 0
    authored_step_one_count = 0
    for skill_name in _EXPERIMENT_LENS_SKILLS:
        vectors = _actual_skill_vectors(skill_name)
        assert len(vectors) == 6, skill_name
        assert vectors[0].id == "missing-context-fields"
        plan = _plan(vectors)
        rendered = backend.exploration_dispatch_renderer.render(plan, vectors)

        assert rendered.router_plan_digest == plan.digest
        assert tuple(rendered.replacements) == tuple(
            vector.id for vector in sorted(vectors, key=lambda item: item.task.task_id)
        )
        for vector in vectors:
            rendered_count += 1
            if vector.id != "missing-context-fields":
                authored_step_one_count += 1
            replacement = rendered.replacements[vector.id]
            assert f'{native_prefix}{vector.role}"' in replacement
            assert f"task_id: {vector.task.task_id}" in replacement
            assert "profile: autoskillit" in replacement
            assert json.dumps(vector.body)[1:-1] in replacement

    assert rendered_count == 108
    assert authored_step_one_count == 90


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    _BACKEND_NATIVE_PREFIX_CASES,
)
def test_all_actual_visualization_vectors_render_each_backend_native_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    filesystem_skills = tuple(
        path.parent.name
        for path in sorted((pkg_root() / "skills_extended").glob("vis-lens-*/SKILL.md"))
    )
    assert filesystem_skills == _VISUALIZATION_LENS_SKILLS

    rendered_count = 0
    retained_count = 0
    for skill_name in _VISUALIZATION_LENS_SKILLS:
        all_vectors = _vectors_from_skill(skill_name)
        vectors = _actual_skill_vectors(skill_name)
        retained_count += len(all_vectors) - len(vectors)
        plan = _plan(vectors)
        rendered = backend.exploration_dispatch_renderer.render(plan, vectors)

        assert rendered.router_plan_digest == plan.digest
        assert tuple(rendered.replacements) == tuple(
            vector.id for vector in sorted(vectors, key=lambda item: item.task.task_id)
        )
        assert set(rendered.replacements).isdisjoint(
            vector.id
            for vector in all_vectors
            if vector.disposition is ExplorationVectorDisposition.RETAINED
        )
        for vector in vectors:
            rendered_count += 1
            replacement = rendered.replacements[vector.id]
            assert f'{native_prefix}{vector.role}"' in replacement
            assert f"task_id: {vector.task.task_id}" in replacement
            assert "profile: autoskillit" in replacement
            assert json.dumps(vector.body)[1:-1] in replacement

    assert rendered_count == 47
    assert retained_count == 11


@pytest.mark.parametrize("selected", _VECTORS, ids=lambda vector: vector.id)
def test_branch_selection_builds_a_plan_only_for_the_selected_vector(
    selected: ExplorationVectorDef,
) -> None:
    plan = _plan((selected,))

    rendered = CodexBackend().exploration_dispatch_renderer.render(plan, (selected,))

    assert plan.tasks == (selected.task,)
    assert set(rendered.replacements) == {selected.id}
    assert selected.applicability is ExplorationVectorApplicabilityId.ALWAYS
