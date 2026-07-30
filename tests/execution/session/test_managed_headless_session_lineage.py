"""Durable managed headless session lineage authority."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageStore,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureMode,
    NativeShellCaptureObservation,
    NativeShellCaptureReason,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.session import (
    DefaultManagedHeadlessSessionLineageStore,
    ManagedHeadlessSessionLineageCASMismatch,
    ManagedHeadlessSessionLineageConflictError,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _create_worker(anchor: str, launch_id: str, mode: str) -> tuple[str, str]:
    try:
        lineage = DefaultManagedHeadlessSessionLineageStore().create(
            lineage_anchor=Path(anchor),
            launch_id=launch_id,
            decision=resolve_native_shell_capture_decision(mode),
            backend="codex",
            session_kind=ManagedHeadlessSessionKind.SKILL,
        )
    except ManagedHeadlessSessionLineageConflictError:
        return ("conflict", mode)
    return ("created", lineage.decision.mode.value)


def _create(
    store: DefaultManagedHeadlessSessionLineageStore,
    anchor: Path,
    *,
    launch_id: str = "1" * 32,
    mode: NativeShellCaptureMode = NativeShellCaptureMode.DIRECT,
    dispatch_id: str | None = None,
):
    return store.create(
        lineage_anchor=anchor,
        launch_id=launch_id,
        decision=resolve_native_shell_capture_decision(mode),
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
        dispatch_id=dispatch_id,
    )


def _cas(lineage) -> dict[str, object]:
    return {
        "expected_generation": lineage.generation,
        "expected_record_digest": lineage.record_digest,
    }


def test_create_round_trip_reference_and_same_parameter_replay(tmp_path: Path) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    created = _create(store, anchor)
    replayed = _create(store, anchor)

    assert replayed == created
    assert created.generation == 0
    assert created.lineage_digest != created.record_digest
    assert store.load(lineage_anchor=anchor, launch_id=created.launch_id) == created
    assert store.load_reference(created.reference) == created
    assert isinstance(store, ManagedHeadlessSessionLineageStore)


def test_duplicate_create_with_different_immutable_parameters_conflicts(
    tmp_path: Path,
) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    _create(store, anchor)
    with pytest.raises(ManagedHeadlessSessionLineageConflictError):
        _create(store, anchor, mode=NativeShellCaptureMode.CAPTURE)


def test_updates_are_generation_and_digest_compare_and_swap(tmp_path: Path) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    created = _create(store, anchor)
    updated = store.append_attempt(
        lineage_anchor=anchor,
        launch_id=created.launch_id,
        attempt_id="2" * 32,
        **_cas(created),
    )
    assert updated.attempt_ids == ("2" * 32,)
    assert updated.generation == 1
    assert updated.lineage_digest == created.lineage_digest
    assert updated.record_digest != created.record_digest

    with pytest.raises(ManagedHeadlessSessionLineageCASMismatch):
        store.append_attempt(
            lineage_anchor=anchor,
            launch_id=created.launch_id,
            attempt_id="3" * 32,
            **_cas(created),
        )
    assert (
        store.append_attempt(
            lineage_anchor=anchor,
            launch_id=created.launch_id,
            attempt_id="2" * 32,
            **_cas(created),
        )
        == updated
    )


def test_candidate_final_dispatch_terminal_and_observation_bindings(
    tmp_path: Path,
) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _create(store, anchor)
    lineage = store.bind_candidate_native_session_id(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        session_id="candidate-thread",
        **_cas(lineage),
    )
    lineage = store.bind_final_native_session_id(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        session_id="final-thread",
        **_cas(lineage),
    )
    assert (
        store.find_by_final_native_session_id(
            lineage_anchor=anchor,
            session_id="final-thread",
        )
        == lineage
    )
    lineage = store.bind_dispatch_id(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        dispatch_id="dispatch-1",
        **_cas(lineage),
    )
    assert (
        store.find_by_dispatch_id(
            lineage_anchor=anchor,
            dispatch_id="dispatch-1",
        )
        == lineage
    )
    observation = NativeShellCaptureObservation(
        attempt_id="4" * 32,
        effective_mode=NativeShellCaptureMode.DIRECT,
        reason=NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
    )
    lineage = store.record_observation(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        observation=observation,
        **_cas(lineage),
    )
    assert lineage.observations == (observation,)
    assert (
        store.record_observation(
            lineage_anchor=anchor,
            launch_id=lineage.launch_id,
            observation=observation,
            **_cas(lineage),
        )
        == lineage
    )
    lineage = store.set_terminal_state(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        terminal_state=ManagedHeadlessSessionTerminalState.SUCCEEDED,
        **_cas(lineage),
    )
    assert lineage.terminal_state is ManagedHeadlessSessionTerminalState.SUCCEEDED


def test_observation_count_overflow_is_bounded_and_counted(tmp_path: Path) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _create(store, anchor)

    for ordinal in range(65):
        attempt_id = f"{ordinal + 1:032x}"
        lineage = store.append_attempt(
            lineage_anchor=anchor,
            launch_id=lineage.launch_id,
            attempt_id=attempt_id,
            **_cas(lineage),
        )
        lineage = store.record_observation(
            lineage_anchor=anchor,
            launch_id=lineage.launch_id,
            observation=NativeShellCaptureObservation(
                attempt_id=attempt_id,
                effective_mode=NativeShellCaptureMode.DIRECT,
                reason=NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
            ),
            **_cas(lineage),
        )

    assert len(lineage.observations) == 64
    assert lineage.dropped_observation_count == 1


def test_resolved_anchor_identity_is_persisted_and_reference_mismatch_rejected(
    tmp_path: Path,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _create(store, alias)
    assert lineage.lineage_anchor == str(physical.resolve())

    wrong = {
        **lineage.reference.to_dict(),
        "anchor_inode": lineage.reference.anchor_inode + 1,
    }
    from autoskillit.core import ManagedHeadlessSessionLineageRef

    with pytest.raises(ValueError, match="identity mismatch"):
        store.load_reference(ManagedHeadlessSessionLineageRef.from_dict(wrong))


def test_corrupt_unsupported_and_digest_mismatched_records_fail_closed(
    tmp_path: Path,
) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _create(store, anchor)
    record = (
        anchor
        / ".autoskillit"
        / "managed-headless-session-lineage"
        / "records"
        / f"{lineage.launch_id}.json"
    )

    record.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        store.load(lineage_anchor=anchor, launch_id=lineage.launch_id)

    lineage = _create(
        store,
        anchor,
        launch_id="5" * 32,
        mode=NativeShellCaptureMode.CAPTURE,
    )
    record = record.with_name(f"{lineage.launch_id}.json")
    value = json.loads(record.read_text(encoding="utf-8"))
    value["schema_version"] = 999
    record.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lineage record"):
        store.load(lineage_anchor=anchor, launch_id=lineage.launch_id)


def test_cross_process_conflicting_creation_has_one_winner(tmp_path: Path) -> None:
    anchor = tmp_path / "project"
    anchor.mkdir()
    launch_id = "6" * 32
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                _create_worker,
                (str(anchor), str(anchor)),
                (launch_id, launch_id),
                ("capture", "direct"),
            )
        )
    assert sorted(result[0] for result in results) == ["conflict", "created"]
