"""Focused server-owned managed-leaf authority tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import (
    MANAGED_JOIN_PARENT_ID_ENV_VAR,
    EffectiveSkillInvocationAuthority,
    HeadlessExecutor,
    RetryReason,
    SessionSkillManager,
    SkillContractError,
    SkillResult,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    ValidatedAddDir,
    WriteBehaviorSpec,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.hooks._session_binding import (
    LoadedSkillEntry,
    read_binding,
    resolve_binding_path,
)
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools.tools_execution._fixed_batch_handlers import (
    _ManagedLeafLaunchAdapter,
)
from autoskillit.server.tools.tools_execution._managed_fixed_batch import ManagedLaunchBinding
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafBinding,
    ManagedLeafProjection,
    _ChildResourceOwnerRequest,
    bind_managed_leaf,
    classify_managed_leaf_workspace,
    plan_managed_leaf_identities,
    project_managed_leaf,
    scoped_child_resource_owner,
)
from autoskillit.workspace import AgentSkillDocument, SkillProjectionContext
from tests.fakes import make_managed_codex_context

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_managed_leaf_planner_and_projection_bind_only_leaf_authority() -> None:
    assignments = (
        ManagedLeafAssignmentInput(
            role="reviewer",
            label="  first review  ",
            runtime_key=" primary ",
            task_prompt="Inspect the first change.",
        ),
        ManagedLeafAssignmentInput(
            role="reviewer",
            label="second review",
            task_prompt="Inspect the second change.",
        ),
    )
    plan = plan_managed_leaf_identities("request-1", assignments)

    assert plan == plan_managed_leaf_identities("request-1", assignments)
    assert plan.assignments[0].label == "first review"
    assert plan.assignments[0].runtime_key == "primary"
    assert plan.assignments[0].assignment_id.startswith(f"{plan.batch_id}:assignment-")
    assert plan.assignments[0].first_run_id.startswith("managed-run-")
    assert plan.assignments[0].generated_home_id.startswith("managed-leaf-")

    adaptation = SkillSemanticAdaptationResult(
        logical_role_mapping={"reviewer": "reviewer"},
        model_effort_policy={"reviewer": ("gpt-5.6-luna", "high")},
    )
    document = AgentSkillDocument(
        content=(
            "Source contract.\n\n"
            "- Use the server-owned managed fixed-batch route to declare, launch, and "
            "join the complete assignment set before parent synthesis.\n"
        ),
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_identity=SkillSourceIdentity(SkillSource.BUNDLED, "review-skill"),
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
    )
    selected_source = LoadedSkillEntry(
        skill_name="review-skill",
        ts="2026-08-28T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"reviewer": 2},
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
    )

    binding = bind_managed_leaf(
        assignment=plan.assignments[0],
        selected_source=selected_source,
        source_document=document,
        adaptation=adaptation,
        default_model="gpt-5.6-sol",
        write_behavior=WriteBehaviorSpec(mode="conditional"),
        read_only=False,
    )
    leaf = project_managed_leaf(binding, document)

    assert leaf.binding is binding
    assert leaf.binding.model == "gpt-5.6-luna"
    assert leaf.leaf_projection_artifact_digest != document.projected_digest
    assert (
        leaf.ledger_attempt_evidence["generated_home_id"] == plan.assignments[0].generated_home_id
    )
    assert (
        leaf.ledger_attempt_evidence["leaf_projection_artifact_digest"]
        == leaf.leaf_projection_artifact_digest
    )
    assert "managed fixed-batch route" not in leaf.prompt
    assert "Inspect the first change." in leaf.prompt
    assert document.content != leaf.prompt

    relocated = replace(document, content="Worker worktree contract.", projected_digest="worker")
    relocated_leaf = project_managed_leaf(binding, document, leaf_document=relocated)
    assert relocated_leaf.binding is binding
    assert relocated_leaf.binding.source_projected_digest == document.projected_digest
    assert "Worker worktree contract." in relocated_leaf.prompt
    assert relocated_leaf.leaf_projection_artifact_digest != leaf.leaf_projection_artifact_digest
    for identity in ("canonical_digest", "semantic_digest", "adaptation_digest"):
        with pytest.raises(SkillContractError, match=f"changed {identity}"):
            project_managed_leaf(
                binding, document, leaf_document=replace(relocated, **{identity: "other"})
            )


def test_managed_leaf_workspace_classification_binds_isolation_and_effects() -> None:
    read_only = classify_managed_leaf_workspace(
        read_only=True,
        write_behavior=WriteBehaviorSpec(),
    )
    idempotent = classify_managed_leaf_workspace(
        read_only=False,
        write_behavior=WriteBehaviorSpec(
            mode="always",
            external_effect="serialized-idempotent",
        ),
    )
    assert not read_only.requires_isolated_worktree
    assert read_only.external_effect == "none"
    assert idempotent.requires_isolated_worktree
    assert idempotent.external_effect == "serialized-idempotent"
    with pytest.raises(SkillContractError, match="requires a declared write_behavior"):
        classify_managed_leaf_workspace(read_only=False, write_behavior=WriteBehaviorSpec())


class _CleanupManager:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def cleanup_session(self, session_id: str) -> bool:
        self.events.append(f"cleanup:{session_id}")
        if self.fail:
            raise RuntimeError("manager cleanup failed")
        return True


@pytest.mark.anyio
async def test_child_resource_owner_prepares_before_yield_and_cleans_after_body(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    materialized = False

    async def prepare(owned_cwd: Path) -> str:
        nonlocal materialized
        assert owned_cwd == tmp_path.resolve()
        events.append("prepare")
        materialized = True
        return "prepared"

    request = _ChildResourceOwnerRequest(
        source_cwd=tmp_path,
        prepare=prepare,
        session_manager=cast(SessionSkillManager, _CleanupManager(events)),
        generated_home_id="headless-owner",
        generated_home_materialized=lambda: materialized,
        copied_snapshot_path=lambda: None,
    )

    async with scoped_child_resource_owner(request) as prepared:
        assert prepared.value == "prepared"
        events.append("execute-and-finalize")

    assert events == ["prepare", "execute-and-finalize", "cleanup:headless-owner"]


@pytest.mark.anyio
async def test_child_resource_owner_attempts_snapshot_cleanup_after_manager_failure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    copied_snapshot = tmp_path / "copied-snapshot"
    copied_snapshot.mkdir()
    materialized = False

    async def prepare(_: Path) -> None:
        nonlocal materialized
        materialized = True

    request = _ChildResourceOwnerRequest(
        source_cwd=tmp_path,
        prepare=prepare,
        session_manager=cast(SessionSkillManager, _CleanupManager(events, fail=True)),
        generated_home_id="headless-owner",
        generated_home_materialized=lambda: materialized,
        copied_snapshot_path=lambda: copied_snapshot,
    )

    with pytest.raises(BaseExceptionGroup, match="Child resource cleanup failed"):
        async with scoped_child_resource_owner(request):
            events.append("execute-and-finalize")

    assert events == ["execute-and-finalize", "cleanup:headless-owner"]
    assert not copied_snapshot.exists()


@pytest.mark.anyio
async def test_child_resource_owner_cleans_materialized_home_after_preparation_failure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    materialized = False

    async def prepare(_: Path) -> None:
        nonlocal materialized
        materialized = True
        raise RuntimeError("projection failed after materialization")

    request = _ChildResourceOwnerRequest(
        source_cwd=tmp_path,
        prepare=prepare,
        session_manager=cast(SessionSkillManager, _CleanupManager(events)),
        generated_home_id="headless-owner",
        generated_home_materialized=lambda: materialized,
        copied_snapshot_path=lambda: None,
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        async with scoped_child_resource_owner(request):
            pytest.fail("owner yielded after failed preparation")

    assert events == ["cleanup:headless-owner"]


@pytest.mark.anyio
async def test_leaf_env_carries_join_identity_equal_to_binding_key(tmp_path: Path) -> None:
    parent_id = "managed-parent-1"
    assignment = plan_managed_leaf_identities(
        "request-1",
        (ManagedLeafAssignmentInput("reviewer", "review", "Inspect the change."),),
    ).assignments[0]
    leaf_session_id = assignment.generated_home_id
    selected_source = LoadedSkillEntry(
        skill_name="review-skill",
        ts="2026-09-21T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"reviewer": 1},
        semantic_digest="semantic-source",
        adaptation_digest="adaptation-source",
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
    )
    projection = ManagedLeafProjection(
        binding=ManagedLeafBinding(
            assignment=assignment,
            source_artifact_digest=selected_source.source_artifact_digest,
            source_artifact_incarnation_id=selected_source.source_artifact_incarnation_id,
            source_projected_digest=selected_source.projected_digest,
            canonical_digest=selected_source.canonical_digest,
            semantic_digest=selected_source.semantic_digest,
            adaptation_digest=selected_source.adaptation_digest,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            workspace=classify_managed_leaf_workspace(
                read_only=True, write_behavior=WriteBehaviorSpec()
            ),
        ),
        prompt="Inspect the change.",
        leaf_projection_artifact_digest="leaf-projection",
    )
    launch = ManagedLaunchBinding(
        request_session_id="transport-session",
        managed_parent_id=parent_id,
        parent_session_id=parent_id,
        caller_key="caller",
        attestation_epoch=0,
        recovery_ready=True,
        selected_source=selected_source,
    )
    adapter = _ManagedLeafLaunchAdapter(
        tool_ctx=cast(ToolContext, SimpleNamespace(project_dir=tmp_path)),
        launch=launch,
        invocation=cast(
            EffectiveSkillInvocationAuthority,
            SimpleNamespace(root=SimpleNamespace(source=SkillSource.BUNDLED_EXTENDED)),
        ),
        projection_context=cast(
            SkillProjectionContext,
            SimpleNamespace(adaptation_context=make_managed_codex_context(parent_id)),
        ),
        source_name="review-skill",
        write_behavior=WriteBehaviorSpec(),
        read_only=True,
        adaptation=SkillSemanticAdaptationResult(),
        source_document=AgentSkillDocument(
            content="Inspect the change.",
            projected_digest=selected_source.projected_digest,
            canonical_digest=selected_source.canonical_digest,
            source_identity=SkillSourceIdentity(SkillSource.BUNDLED_EXTENDED, "review-skill"),
        ),
    )
    adapter._write_leaf_binding(leaf_session_id, projection)

    executor = AsyncMock()
    executor.run.return_value = SkillResult(
        success=True,
        result="done",
        session_id="leaf-thread",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    backend = CodexBackend()
    await adapter._execute_leaf(
        cast(HeadlessExecutor, executor),
        backend,
        tmp_path,
        projection,
        cast(ValidatedAddDir, tmp_path),
        backend.conventions,
    )

    assert executor.run.await_args.kwargs["provider_extras"] == {
        MANAGED_JOIN_PARENT_ID_ENV_VAR: leaf_session_id
    }
    binding = read_binding(resolve_binding_path(str(tmp_path), leaf_session_id))
    assert binding is not None
    assert binding.session_id == leaf_session_id
    assert binding.managed_parent_id == parent_id
