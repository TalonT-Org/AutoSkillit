"""Focused server-owned managed-leaf authority tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from autoskillit.core import (
    SessionSkillManager,
    SkillContractError,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    WriteBehaviorSpec,
)
from autoskillit.hooks._session_binding import LoadedSkillEntry
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    _ChildResourceOwnerRequest,
    bind_managed_leaf,
    classify_managed_leaf_workspace,
    may_retry_managed_leaf,
    plan_managed_leaf_identities,
    project_managed_leaf,
    scoped_child_resource_owner,
)
from autoskillit.workspace import AgentSkillDocument

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

    assert leaf.resume_session_id == ""
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


def test_managed_leaf_workspace_classification_is_safe_for_effect_retries() -> None:
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
    unknown = classify_managed_leaf_workspace(
        read_only=False,
        write_behavior=WriteBehaviorSpec(
            mode="always",
            external_effect="serialized-unknown-completion",
        ),
    )

    assert read_only.shared_workspace
    assert idempotent.requires_isolated_worktree
    assert may_retry_managed_leaf(idempotent, launched=False, verified_non_execution=False)
    assert may_retry_managed_leaf(idempotent, launched=True, verified_non_execution=True)
    assert not may_retry_managed_leaf(idempotent, launched=True, verified_non_execution=False)
    assert not may_retry_managed_leaf(unknown, launched=False, verified_non_execution=False)
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
