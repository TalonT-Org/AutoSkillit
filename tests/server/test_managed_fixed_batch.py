"""Focused lifecycle tests for the server-owned managed fixed-batch service."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from autoskillit.core import (
    DefaultManagedWorkerCapacity,
    SkillContractError,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    WriteBehaviorSpec,
)
from autoskillit.hooks._join_ledger import aggregate_batch
from autoskillit.hooks._session_binding import LoadedSkillEntry
from autoskillit.pipeline import DefaultBackgroundSupervisor
from autoskillit.server.tools.tools_execution._managed_fixed_batch import (
    ManagedFixedBatchLaunchBinding,
    ManagedFixedBatchService,
    ManagedLaunchBinding,
    ManagedLeafLaunchResult,
)
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafPreparedLaunch,
)
from autoskillit.workspace import AgentSkillDocument

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@asynccontextmanager
async def _prepared_leaf(projection, result: ManagedLeafLaunchResult):
    async def execute() -> ManagedLeafLaunchResult:
        return result

    yield ManagedLeafPreparedLaunch(
        ledger_attempt_evidence=projection.ledger_attempt_evidence,
        execute=execute,
    )


def _binding(tmp_path, launch_leaf):
    adaptation = SkillSemanticAdaptationResult(
        logical_role_mapping={"worker": "worker"},
        model_effort_policy={"worker": ("gpt-5.6-luna", "high")},
    )
    source = LoadedSkillEntry(
        skill_name="fixed-batch-skill",
        ts="2026-08-28T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"worker": 2},
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
    )
    document = AgentSkillDocument(
        content="Source contract.\n",
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_identity=SkillSourceIdentity(SkillSource.BUNDLED, "fixed-batch-skill"),
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
    )
    return ManagedFixedBatchLaunchBinding(
        launch=ManagedLaunchBinding(
            request_session_id="request-session",
            managed_parent_id="managed-parent",
            parent_session_id="request-session",
            caller_key="request-key",
            attestation_epoch=3,
            recovery_ready=True,
            selected_source=source,
        ),
        flag_dir=tmp_path / "channel",
        source_document=document,
        adaptation=adaptation,
        assignments=(
            ManagedLeafAssignmentInput(role="worker", label="first", task_prompt="first task"),
            ManagedLeafAssignmentInput(role="worker", label="second", task_prompt="second task"),
        ),
        default_model="gpt-5.6-sol",
        write_behavior=WriteBehaviorSpec(),
        read_only=True,
        launch_leaf=launch_leaf,
    )


@pytest.mark.anyio
async def test_supervisor_opens_once_replays_and_releases_each_owned_permit(tmp_path) -> None:
    capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    service = ManagedFixedBatchService(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )
    seen_permits: list[str] = []

    def launch_leaf(projection, permit):
        seen_permits.append(permit.permit_id)
        assert projection.resume_session_id == ""
        return _prepared_leaf(projection, ManagedLeafLaunchResult())

    binding = _binding(tmp_path, launch_leaf)
    assert await service.reconcile_startup()

    first = await service.run(binding)
    replay = await service.run(binding)

    assert first.wave_outcome == "complete"
    assert replay.replayed is True
    assert replay.batch_id == first.batch_id
    assert len(seen_permits) == 2
    assert capacity.active_count == 0
    assert aggregate_batch(binding.flag_dir, batch_id=first.batch_id) == "complete"


@pytest.mark.anyio
async def test_unresolved_recovery_debt_keeps_managed_route_closed(tmp_path) -> None:
    capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    service = ManagedFixedBatchService(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )

    def launch_leaf(projection, _permit):
        return _prepared_leaf(projection, ManagedLeafLaunchResult())

    binding = _binding(tmp_path, launch_leaf)
    with pytest.raises(SkillContractError, match="recovery"):
        await service.run(binding)


@pytest.mark.anyio
async def test_owner_cleanup_precedes_settlement_and_permit_release(tmp_path) -> None:
    events: list[str] = []

    class RecordingCapacity(DefaultManagedWorkerCapacity):
        def release(self, permit) -> None:
            events.append("permit-release")
            super().release(permit)

    capacity = RecordingCapacity(max_concurrent=1)
    service = ManagedFixedBatchService(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )

    @asynccontextmanager
    async def launch_leaf(projection, _permit):
        assert (tmp_path / "state" / "recovery.json").is_file()
        events.append("owner-enter")

        async def execute() -> ManagedLeafLaunchResult:
            events.append("execute")
            return ManagedLeafLaunchResult()

        async def finalize(_result: ManagedLeafLaunchResult) -> None:
            events.append("finalize")

        try:
            yield ManagedLeafPreparedLaunch(
                ledger_attempt_evidence=projection.ledger_attempt_evidence,
                execute=execute,
                finalize=finalize,
            )
        finally:
            events.append("owner-cleanup")

    assert await service.reconcile_startup()
    result = await service.run(_binding(tmp_path, launch_leaf))

    assert result.wave_outcome == "complete"
    assert events == [
        "owner-enter",
        "execute",
        "finalize",
        "owner-cleanup",
        "permit-release",
        "owner-enter",
        "execute",
        "finalize",
        "owner-cleanup",
        "permit-release",
    ]
