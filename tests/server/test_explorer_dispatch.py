"""Focused contracts for backend-neutral explorer dispatch materialization."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    BackendPinResolution,
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    ProfileActivation,
    RelationshipKind,
    RepositoryProfileId,
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.server.tools._execution_helpers import bind_projection_backend
from autoskillit.workspace import EffectiveSkillInvocation, SkillInfo
from autoskillit.workspace._projected_artifact.materialization import (
    SkillProjectionContext,
    project_agent_skill_document,
)

pytestmark = [
    pytest.mark.layer("server"),
    pytest.mark.feature("exploration"),
    pytest.mark.small,
]


def _vector() -> ExplorationVectorDef:
    return ExplorationVectorDef(
        id="semantic-navigation",
        disposition=ExplorationVectorDisposition.MIGRATED,
        rationale="Use typed semantic evidence.",
        applicability=ExplorationVectorApplicabilityId.ALWAYS,
        role="semantic-code-navigator",
        profile=RepositoryProfileId.AUTOSKILLIT,
        relationship_classes=(RelationshipKind.DEFINES, RelationshipKind.REFERENCES),
        task=ExplorationTaskSpec(
            "semantic-task",
            "semantic-frontier",
            RepositoryProfileId.AUTOSKILLIT,
            scope=("src",),
        ),
        max_results=25,
        max_report_bytes=8_000,
        evidence_version=1,
        native_dispatch=True,
        body="Trace definitions and references with bounded evidence.",
    )


def _plan(vector: ExplorationVectorDef) -> ExplorationRouterPlan:
    return ExplorationRouterPlan(
        snapshot=None,
        tasks=(vector.task,),
        activations=(
            ProfileActivation(
                vector.profile,
                ExplorationApplicability.APPLICABLE,
                "authoring applicability:always",
            ),
        ),
    )


def _skill(tmp_path: Path, *, vector: ExplorationVectorDef | None) -> SkillInfo:
    marker_body = ""
    vectors: tuple[ExplorationVectorDef, ...] = ()
    if vector is not None:
        marker_body = (
            f"\n{vector.marker_line}\n{vector.body}\n<!-- /autoskillit:exploration-vector -->"
        )
        vectors = (vector,)
    content = (
        "---\nname: dispatch-pilot\ndescription: Dispatch pilot.\n"
        "execution_role: session\n---\n# Dispatch pilot"
        f"{marker_body}\n"
    )
    return SkillInfo(
        name="dispatch-pilot",
        source=SkillSource.PROJECT_LOCAL,
        path=tmp_path / "SKILL.md",
        canonical_content=content,
        exploration_vectors=vectors,
    )


def _context(tmp_path: Path, skill: SkillInfo, backend=None) -> SkillProjectionContext:
    invocation = EffectiveSkillInvocation(
        root=skill,
        closure=(skill,),
        capability_union=frozenset(),
        project_root=tmp_path,
        execution_role=SkillExecutionRole.SESSION,
    )
    return SkillProjectionContext(cwd=tmp_path, invocation=invocation, backend=backend)


def _auto_vector(*, applicability: ExplorationVectorApplicabilityId) -> ExplorationVectorDef:
    vector = _vector()
    return replace(
        vector,
        profile=RepositoryProfileId.AUTO,
        applicability=applicability,
        task=replace(vector.task, profile=RepositoryProfileId.AUTO),
    )


def test_claude_and_codex_bind_identical_neutral_and_role_digests() -> None:
    vector = _vector()
    plan = _plan(vector)

    claude = ClaudeCodeBackend().exploration_dispatch_renderer.render(plan, (vector,))
    codex = CodexBackend().exploration_dispatch_renderer.render(plan, (vector,))

    assert claude.router_plan_digest == codex.router_plan_digest == plan.digest
    assert claude.role_definition_digests == codex.role_definition_digests
    claude_call = claude.replacements[vector.id].splitlines()[-1]
    codex_call = codex.replacements[vector.id].splitlines()[-1]
    assert claude_call.startswith(
        'Agent(subagent_type="autoskillit:semantic-code-navigator", description='
    )
    assert ", prompt=" in claude_call
    assert codex_call.startswith('spawn_agent(agent_type="semantic-code-navigator", message=')
    assert "run_agent" not in claude_call + codex_call


def test_projection_materializes_only_after_backend_binding(tmp_path: Path) -> None:
    vector = _vector()
    skill = _skill(tmp_path, vector=vector)

    with pytest.raises(SkillContractError, match="bound backend and conventions"):
        project_agent_skill_document(skill, _context(tmp_path, skill))

    document = project_agent_skill_document(
        skill,
        _context(tmp_path, skill, ClaudeCodeBackend()),
    )
    assert 'Agent(subagent_type="autoskillit:semantic-code-navigator"' in document.content
    assert (
        "Submit this typed task packet to the deterministic exploration router" in document.content
    )
    assert "Reclassify every newly discovered cross-leaf frontier explicitly" in document.content
    assert "Wait for every dispatched leaf" in document.content
    assert "Retain final synthesis" in document.content
    marker_body = document.content.split(vector.marker_line, 1)[1].split(
        "<!-- /autoskillit:exploration-vector -->", 1
    )[0]
    assert f"\n{vector.body}\n" not in marker_body
    assert marker_body.rstrip().splitlines()[-1].startswith("Agent(")


def test_backendless_projection_remains_valid_for_skill_without_vectors(tmp_path: Path) -> None:
    skill = _skill(tmp_path, vector=None)

    document = project_agent_skill_document(skill, _context(tmp_path, skill))

    assert "# Dispatch pilot" in document.content
    assert "Parent routing contract" not in document.content


def test_projection_binding_rejects_backend_pin_disagreement(tmp_path: Path) -> None:
    vector = _vector()
    skill = _skill(tmp_path, vector=vector)
    context = _context(tmp_path, skill)

    with pytest.raises(SkillContractError, match="disagrees with resolved backend authority"):
        bind_projection_backend(
            context,
            ClaudeCodeBackend(),
            resolution=BackendPinResolution("codex", "recipe_step", "recipe.step_overrides"),
        )


def test_projection_binding_adds_stable_non_digest_launch_reference(tmp_path: Path) -> None:
    vector = _vector()
    skill = _skill(tmp_path, vector=vector)

    context = bind_projection_backend(
        _context(tmp_path, skill),
        CodexBackend(),
        resolution=BackendPinResolution("codex", "recipe_step", "recipe.step_overrides"),
    )

    assert context.exploration_launch_context_ref == "skill:dispatch-pilot"


@pytest.mark.parametrize(
    ("resolved_profile", "expected"),
    [
        (RepositoryProfileId.AUTOSKILLIT, "profile: autoskillit"),
        (RepositoryProfileId.LANGUAGE_NEUTRAL, "profile: language-neutral"),
    ],
)
def test_profile_auto_materializes_only_the_trusted_resolved_profile(
    tmp_path: Path,
    resolved_profile: RepositoryProfileId,
    expected: str,
) -> None:
    vector = _auto_vector(applicability=ExplorationVectorApplicabilityId.ALWAYS)
    skill = _skill(tmp_path, vector=vector)
    context = replace(
        _context(tmp_path, skill, CodexBackend()),
        resolved_exploration_profile=resolved_profile,
    )

    document = project_agent_skill_document(skill, context)

    assert expected in document.content
    assert "\nprofile: auto\n" not in document.content


def test_closed_applicability_excludes_deep_vector_from_standard_projection(
    tmp_path: Path,
) -> None:
    vector = _auto_vector(
        applicability=ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP
    )
    skill = _skill(tmp_path, vector=vector)
    standard = replace(
        _context(tmp_path, skill, CodexBackend()),
        resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
    )
    deep = replace(
        standard,
        active_exploration_applicabilities=frozenset(
            {
                ExplorationVectorApplicabilityId.ALWAYS,
                ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP,
            }
        ),
    )

    standard_document = project_agent_skill_document(skill, standard)
    deep_document = project_agent_skill_document(skill, deep)

    assert "not applicable to the current invocation" in standard_document.content
    assert "spawn_agent(" not in standard_document.content
    assert "task_id: semantic-task" not in standard_document.content
    assert "spawn_agent(" in deep_document.content
    assert "task_id: semantic-task" in deep_document.content
