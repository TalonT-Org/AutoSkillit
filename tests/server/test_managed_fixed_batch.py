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
    write_versioned_json,
)
from autoskillit.hooks._join_ledger import aggregate_batch
from autoskillit.hooks._session_binding import LoadedSkillEntry
from autoskillit.pipeline import DefaultBackgroundSupervisor
from autoskillit.server.tools.tools_execution._managed_fixed_batch import (
    DefaultManagedFixedBatchSupervisor,
    ManagedFixedBatchLaunchBinding,
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
    service = DefaultManagedFixedBatchSupervisor(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )
    seen_permits: list[str] = []

    def launch_leaf(projection, permit):
        seen_permits.append(permit.permit_id)
        assert projection.binding.assignment.label in {"first", "second"}
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
    service = DefaultManagedFixedBatchSupervisor(
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
    service = DefaultManagedFixedBatchSupervisor(
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
    binding = _binding(tmp_path, launch_leaf)
    result = await service.run(binding)

    assert result.wave_outcome == "complete"
    expected_assignment_events = [
        "owner-enter",
        "execute",
        "finalize",
        "owner-cleanup",
        "permit-release",
    ]
    assert events == expected_assignment_events * len(binding.assignments)


@pytest.mark.anyio
async def test_unadmitted_settlement_failure_does_not_leak_capacity(tmp_path, monkeypatch) -> None:
    capacity = DefaultManagedWorkerCapacity(max_concurrent=2)
    service = DefaultManagedFixedBatchSupervisor(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )

    def launch_leaf(_projection, _permit):
        raise RuntimeError("preparation failed")

    def fail_settlement(*_args, **_kwargs):
        raise SkillContractError("settlement failed")

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._managed_fixed_batch."
        "settle_unadmitted_assignment",
        fail_settlement,
    )
    assert await service.reconcile_startup()

    result = await service.run(_binding(tmp_path, launch_leaf))

    assert result.wave_outcome == "pending"
    assert capacity.active_count == 0


@pytest.mark.anyio
async def test_capacity_acquisition_failure_terminalizes_assignment(tmp_path, monkeypatch) -> None:
    capacity = DefaultManagedWorkerCapacity(max_concurrent=2)

    async def fail_acquire(_owner):
        raise RuntimeError("capacity unavailable")

    monkeypatch.setattr(capacity, "acquire", fail_acquire)
    service = DefaultManagedFixedBatchSupervisor(
        capacity=capacity,
        background=DefaultBackgroundSupervisor(),
        state_root=tmp_path / "state",
    )

    def launch_leaf(_projection, _permit):
        raise AssertionError("launch must not run without capacity")

    assert await service.reconcile_startup()

    result = await service.run(_binding(tmp_path, launch_leaf))

    assert result.wave_outcome == "launch_failed"
    assert capacity.active_count == 0


@pytest.mark.anyio
async def test_recovery_rejects_malformed_persisted_string_fields(tmp_path) -> None:
    state_root = tmp_path / "state"
    write_versioned_json(
        state_root / "recovery.json",
        {
            "debt": [
                {
                    "owner": ["batch", "assignment", "run"],
                    "permit_id": "permit-1",
                    "flag_dir": str(tmp_path / "channel"),
                    "request_session_id": 7,
                    "managed_parent_id": "parent",
                    "batch_id": "batch",
                    "assignment_id": "assignment",
                    "attempt_id": "attempt",
                    "run_id": "run",
                }
            ]
        },
        1,
    )
    service = DefaultManagedFixedBatchSupervisor(
        capacity=DefaultManagedWorkerCapacity(),
        background=DefaultBackgroundSupervisor(),
        state_root=state_root,
    )

    assert not await service.reconcile_startup()
    assert "request_session_id must be a non-empty string" in service.recovery_diagnostic
