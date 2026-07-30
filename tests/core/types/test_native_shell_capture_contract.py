"""Closed native-shell launch-control and lineage value contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStatus,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    pop_native_shell_capture_decision,
    resolve_native_shell_capture_decision,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_native_shell_capture_mode_is_closed_and_fresh_default_is_capture() -> None:
    assert {item.value for item in NativeShellCaptureMode} == {"capture", "direct"}
    decision = resolve_native_shell_capture_decision(None)
    assert decision == NativeShellCaptureDecision(
        mode=NativeShellCaptureMode.CAPTURE,
        reason=NativeShellCaptureReason.FRESH_DEFAULT,
        lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("capture", NativeShellCaptureMode.CAPTURE),
        ("direct", NativeShellCaptureMode.DIRECT),
        (NativeShellCaptureMode.DIRECT, NativeShellCaptureMode.DIRECT),
    ],
)
def test_explicit_mode_resolution_is_typed(
    value: NativeShellCaptureMode | str,
    expected: NativeShellCaptureMode,
) -> None:
    decision = resolve_native_shell_capture_decision(value)
    assert decision.mode is expected
    assert decision.reason is NativeShellCaptureReason.EXPLICIT_ARGUMENT


@pytest.mark.parametrize("value", ["DIRECT", "", "enabled", 1, True])
def test_explicit_mode_resolution_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="capture.*direct"):
        resolve_native_shell_capture_decision(value)  # type: ignore[arg-type]


def test_environment_intake_consumes_once_and_invalid_input_fails_closed() -> None:
    environ = {"AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE": "direct"}
    first = pop_native_shell_capture_decision(environ)
    second = pop_native_shell_capture_decision(environ)
    assert first.mode is NativeShellCaptureMode.DIRECT
    assert first.reason is NativeShellCaptureReason.ENVIRONMENT
    assert second.mode is NativeShellCaptureMode.CAPTURE
    assert second.reason is NativeShellCaptureReason.FRESH_DEFAULT
    assert environ == {}

    invalid_environ = {"AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE": "maybe"}
    invalid = pop_native_shell_capture_decision(invalid_environ)
    assert invalid.mode is NativeShellCaptureMode.CAPTURE
    assert invalid.reason is NativeShellCaptureReason.INVALID_ENVIRONMENT
    assert invalid.lineage_status is ManagedHeadlessSessionLineageStatus.CORRUPT
    assert invalid_environ == {}


def test_decision_and_reference_round_trip_exact_shapes() -> None:
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    assert NativeShellCaptureDecision.from_dict(decision.to_dict()) == decision
    reference = ManagedHeadlessSessionLineageRef(
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor="/physical/project",
        anchor_device=12,
        anchor_inode=34,
    )
    assert ManagedHeadlessSessionLineageRef.from_dict(reference.to_dict()) == reference

    with pytest.raises(FrozenInstanceError):
        reference.launch_id = "c" * 32  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {
            "schema_version": 1,
            "launch_id": "a" * 32,
            "lineage_digest": "b" * 64,
            "lineage_anchor": "/physical/project",
            "anchor_device": True,
            "anchor_inode": 34,
        },
        {
            "schema_version": 1,
            "launch_id": "a" * 32,
            "lineage_digest": "b" * 64,
            "lineage_anchor": "relative",
            "anchor_device": 12,
            "anchor_inode": 34,
        },
    ],
)
def test_reference_rejects_invalid_serialized_values(bad: object) -> None:
    with pytest.raises(ValueError, match="lineage reference"):
        ManagedHeadlessSessionLineageRef.from_dict(bad)
