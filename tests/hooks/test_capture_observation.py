"""Lineage-bound runner observation tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.execution.session._managed_headless_session_lineage as lineage_store_module
from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.headless._managed import _ManagedLineageObserver
from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
from autoskillit.hooks._capture_artifacts import (
    CaptureLineageRef,
    record_runner_observation,
    run_capture,
    validate_lineage_reference,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_LAUNCH_ID = "1" * 32
_ATTEMPT_ID = "2" * 32
_CAPTURE_ID = "0123456789abcdef"


def _lineage(
    tmp_path: Path,
    *,
    mode: NativeShellCaptureMode = NativeShellCaptureMode.DIRECT,
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
        decision=resolve_native_shell_capture_decision(mode),
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


def _record_path(anchor: Path) -> Path:
    return (
        anchor
        / ".autoskillit"
        / "managed-headless-session-lineage"
        / "records"
        / f"{_LAUNCH_ID}.json"
    )


def _observation_root(anchor: Path) -> Path:
    return anchor / ".autoskillit" / "managed-headless-session-lineage" / "runner-observations"


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


@pytest.mark.parametrize(
    "failure",
    [
        "missing_record",
        "duplicate_record_field",
        "mismatched_record",
        "mismatched_record_digest",
        "mismatched_digest",
        "mismatched_anchor_identity",
    ],
)
def test_invalid_record_and_reference_identities_cannot_write_marker(
    failure: str,
    tmp_path: Path,
) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    record_path = _record_path(anchor)
    invalid = reference
    if failure == "missing_record":
        record_path.unlink()
    elif failure == "duplicate_record_field":
        raw = record_path.read_text()
        record_path.write_text(f'{{"launch_id":"{"f" * 32}",{raw[1:]}')
    elif failure == "mismatched_record":
        record = json.loads(record_path.read_text())
        record["launch_id"] = "f" * 32
        record_path.write_text(
            json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )
    elif failure == "mismatched_record_digest":
        record = json.loads(record_path.read_text())
        record["record_digest"] = "f" * 64
        record_path.write_text(
            json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )
    elif failure == "mismatched_digest":
        invalid = replace(reference, lineage_digest="f" * 64)
    else:
        invalid = replace(reference, anchor_inode=reference.anchor_inode + 1)

    assert not validate_lineage_reference(invalid, _ATTEMPT_ID)
    assert not record_runner_observation(
        invalid,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=False,
    )
    assert not _observation_root(anchor).exists()


def test_unknown_attempt_cannot_write_marker(tmp_path: Path) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    assert not validate_lineage_reference(reference, "3" * 32)
    assert not record_runner_observation(
        reference,
        "3" * 32,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=False,
    )
    assert not _observation_root(anchor).exists()


def test_arbitrary_and_traversal_anchor_paths_fail_closed(tmp_path: Path) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    arbitrary = tmp_path / "arbitrary"
    arbitrary.mkdir()
    arbitrary_stat = arbitrary.stat()
    invalid_references = (
        replace(
            reference,
            lineage_anchor=str(arbitrary),
            anchor_device=arbitrary_stat.st_dev,
            anchor_inode=arbitrary_stat.st_ino,
        ),
        replace(reference, lineage_anchor=str(anchor / ".." / anchor.name)),
    )

    for invalid in invalid_references:
        assert not validate_lineage_reference(invalid, _ATTEMPT_ID)
        assert not record_runner_observation(
            invalid,
            _ATTEMPT_ID,
            effective_mode="direct",
            reason="launch_authorized_direct",
            project_policy_disabled=False,
        )
    assert not _observation_root(anchor).exists()


@pytest.mark.parametrize("symlink_component", ["records", "runner-observations"])
def test_below_anchor_symlink_substitution_fails_closed(
    symlink_component: str,
    tmp_path: Path,
) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    lineage_root = anchor / ".autoskillit" / "managed-headless-session-lineage"
    component = lineage_root / symlink_component
    external = tmp_path / f"external-{symlink_component}"
    if component.exists():
        component.rename(external)
    else:
        external.mkdir()
    component.symlink_to(external, target_is_directory=True)

    if symlink_component == "records":
        assert not validate_lineage_reference(reference, _ATTEMPT_ID)
    else:
        assert validate_lineage_reference(reference, _ATTEMPT_ID)
    assert not record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=False,
    )
    if symlink_component == "runner-observations":
        assert list(external.iterdir()) == []


@pytest.mark.parametrize(
    ("effective_mode", "reason", "project_policy_disabled"),
    [
        ("unknown", "launch_authorized_direct", False),
        ("direct", "unknown", False),
        ("direct", "launch_authorized_direct", 1),
    ],
)
def test_unknown_observation_values_fail_closed_without_output(
    effective_mode: object,
    reason: object,
    project_policy_disabled: object,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    assert not record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode=effective_mode,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        project_policy_disabled=project_policy_disabled,  # type: ignore[arg-type]
    )
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not _observation_root(anchor).exists()


def test_invalid_observation_authority_does_not_change_command_output(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    anchor, _store, _lineage_value, reference = _lineage(tmp_path)
    invalid = replace(reference, lineage_digest="f" * 64)

    assert (
        run_capture(
            "printf unchanged",
            str(anchor),
            _CAPTURE_ID,
            requested_mode="capture",
            attempt_id=_ATTEMPT_ID,
            lineage_ref=invalid,
        )
        == 0
    )

    captured = capfd.readouterr()
    assert captured.out.startswith("unchanged")
    assert captured.err == ""
    assert not _observation_root(anchor).exists()


def test_command_cwd_remains_separate_from_exact_lineage_anchor(tmp_path: Path) -> None:
    lineage_anchor, store, lineage, reference = _lineage(tmp_path)
    command_cwd = tmp_path / "command-cwd"
    command_cwd.mkdir()

    assert (
        run_capture(
            "pwd > observed-cwd",
            str(command_cwd),
            _CAPTURE_ID,
            requested_mode="direct",
            attempt_id=_ATTEMPT_ID,
            lineage_ref=reference,
        )
        == 0
    )

    assert (command_cwd / "observed-cwd").read_text().strip() == str(command_cwd.resolve())
    collected = store.collect_runner_observations(lineage.reference)
    assert len(collected.observations) == 1
    assert collected.observations[0].attempt_id == _ATTEMPT_ID
    assert _observation_root(lineage_anchor).is_dir()
    assert not (command_cwd / ".autoskillit").exists()


@pytest.mark.parametrize(
    (
        "launch_mode",
        "policy_disabled",
        "effective_mode",
        "primary_reason",
        "attributions",
    ),
    [
        (
            NativeShellCaptureMode.CAPTURE,
            False,
            NativeShellCaptureMode.CAPTURE,
            NativeShellCaptureReason.CAPTURE_ENABLED,
            (NativeShellCaptureReason.CAPTURE_ENABLED,),
        ),
        (
            NativeShellCaptureMode.CAPTURE,
            True,
            NativeShellCaptureMode.DIRECT,
            NativeShellCaptureReason.PROJECT_POLICY_DISABLED,
            (NativeShellCaptureReason.PROJECT_POLICY_DISABLED,),
        ),
        (
            NativeShellCaptureMode.DIRECT,
            False,
            NativeShellCaptureMode.DIRECT,
            NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
            (NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,),
        ),
        (
            NativeShellCaptureMode.DIRECT,
            True,
            NativeShellCaptureMode.DIRECT,
            NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
            (
                NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
                NativeShellCaptureReason.PROJECT_POLICY_DISABLED,
            ),
        ),
    ],
)
def test_terminal_diagnostic_preserves_policy_precedence_and_attribution(
    launch_mode: NativeShellCaptureMode,
    policy_disabled: bool,
    effective_mode: NativeShellCaptureMode,
    primary_reason: NativeShellCaptureReason,
    attributions: tuple[NativeShellCaptureReason, ...],
    tmp_path: Path,
) -> None:
    _anchor, store, lineage, reference = _lineage(tmp_path, mode=launch_mode)
    assert record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode=effective_mode.value,
        reason=primary_reason.value,
        project_policy_disabled=policy_disabled,
    )
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=lineage.decision,
        reference=lineage.reference,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    assert observer is not None

    diagnostic = observer.capture_diagnostic()

    assert diagnostic.requested_mode is launch_mode
    assert diagnostic.effective_mode is effective_mode
    assert diagnostic.primary_reason is primary_reason
    assert diagnostic.attributions == attributions


def test_encoded_byte_overflow_is_counted_once_across_repeated_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _anchor, store, lineage, reference = _lineage(tmp_path)
    assert record_runner_observation(
        reference,
        _ATTEMPT_ID,
        effective_mode="direct",
        reason="launch_authorized_direct",
        project_policy_disabled=False,
    )
    monkeypatch.setattr(lineage_store_module, "_MAX_OBSERVATION_BYTES", 1)

    first = store.collect_runner_observations(lineage.reference)
    second = store.collect_runner_observations(lineage.reference)

    assert first.observations == second.observations == ()
    assert first.dropped_observation_count == second.dropped_observation_count == 1
