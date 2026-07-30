"""Lineage-bound runner observation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
from autoskillit.hooks._capture._observation import (
    record_runner_observation,
    validate_lineage_reference,
)
from autoskillit.hooks._capture_contract import CaptureLineageRef

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_LAUNCH_ID = "1" * 32
_ATTEMPT_ID = "2" * 32


def _lineage(
    tmp_path: Path,
) -> tuple[
    Path,
    DefaultManagedHeadlessSessionLineageStore,
    ManagedHeadlessSessionLineage,
    CaptureLineageRef,
]:
    anchor = tmp_path / "project"
    anchor.mkdir()
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = store.create(
        lineage_anchor=anchor,
        launch_id=_LAUNCH_ID,
        decision=resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT),
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    lineage = store.append_attempt(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        attempt_id=_ATTEMPT_ID,
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    reference = CaptureLineageRef(
        schema_version=lineage.reference.schema_version,
        launch_id=lineage.reference.launch_id,
        lineage_digest=lineage.reference.lineage_digest,
        lineage_anchor=lineage.reference.lineage_anchor,
        anchor_device=lineage.reference.anchor_device,
        anchor_inode=lineage.reference.anchor_inode,
    )
    return anchor, store, lineage, reference


def test_valid_marker_round_trips_into_durable_lineage(tmp_path: Path) -> None:
    _anchor, store, lineage, reference = _lineage(tmp_path)
    assert validate_lineage_reference(reference, _ATTEMPT_ID)
    assert record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=True,
    )
    assert record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=True,
    )

    collected = store.collect_runner_observations(lineage.reference)
    assert len(collected.observations) == 1
    observation = collected.observations[0]
    assert observation.attempt_id == _ATTEMPT_ID
    assert observation.effective_mode is NativeShellCaptureMode.DIRECT
    assert observation.project_policy_disabled


def test_mismatched_digest_cannot_write_marker(tmp_path: Path) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    invalid = CaptureLineageRef(
        schema_version=reference.schema_version,
        launch_id=reference.launch_id,
        lineage_digest="f" * 64,
        lineage_anchor=reference.lineage_anchor,
        anchor_device=reference.anchor_device,
        anchor_inode=reference.anchor_inode,
    )
    assert not validate_lineage_reference(invalid, _ATTEMPT_ID)
    assert not record_runner_observation(
        invalid,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=False,
    )
    assert not (
        anchor / ".autoskillit" / "managed-headless-session-lineage" / "runner-observations"
    ).exists()


def test_unknown_attempt_cannot_write_marker(tmp_path: Path) -> None:
    _anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    assert not validate_lineage_reference(reference, "3" * 32)
