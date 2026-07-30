"""Managed native-shell diagnostic projection and event tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
import structlog.testing

from autoskillit.core import (
    ManagedHeadlessSessionLineageStatus,
    NativeShellCaptureDiagnostic,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless._managed import _attempt as diagnostics

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _Observer:
    def __init__(self, diagnostic: NativeShellCaptureDiagnostic) -> None:
        self.diagnostic = diagnostic

    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        return self.diagnostic


class _FailingObserver:
    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        raise ValueError("invalid marker")


def _diagnostic() -> NativeShellCaptureDiagnostic:
    return NativeShellCaptureDiagnostic(
        requested_mode=NativeShellCaptureMode.DIRECT,
        effective_mode=NativeShellCaptureMode.DIRECT,
        primary_reason=NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
        attributions=(
            NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
            NativeShellCaptureReason.PROJECT_POLICY_DISABLED,
        ),
        resolution_reason=NativeShellCaptureReason.EXPLICIT_ARGUMENT,
        lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
        launch_id="1" * 32,
        attempt_id="2" * 32,
    )


def _successful_result() -> SkillResult:
    return SkillResult(
        success=True,
        result="done",
        session_id="session-1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def test_launch_and_exit_events_share_the_immutable_projection() -> None:
    diagnostic = _diagnostic()
    observer = cast(Any, _Observer(diagnostic))

    with structlog.testing.capture_logs() as logs:
        diagnostics.log_launch(observer)
        diagnostics.log_exit(diagnostic, _successful_result())

    launch = next(item for item in logs if item["event"] == "headless_session_launch")
    exit_event = next(item for item in logs if item["event"] == "headless_session_exit")
    assert launch["event_id"] == diagnostic.event_id(stage="launch")
    assert exit_event["event_id"] == diagnostic.event_id(stage="exit")
    assert launch["native_shell_capture"]["requested_mode"] == "direct"
    assert exit_event["native_shell_capture"]["requested_mode"] == "direct"
    assert exit_event["success"] is True


def test_invalid_observation_state_does_not_disrupt_execution() -> None:
    observer = cast(Any, _FailingObserver())
    with structlog.testing.capture_logs() as logs:
        assert diagnostics.capture(observer) is None
    assert any(item["event"] == "native_shell_capture_diagnostic_failed" for item in logs)


def test_cancelled_exit_uses_the_common_terminal_event() -> None:
    with structlog.testing.capture_logs() as logs:
        diagnostics.log_cancelled(_diagnostic())
    event = next(item for item in logs if item["event"] == "headless_session_exit")
    assert event["success"] is False
    assert event["needs_retry"] is True
    assert event["subtype"] == "cancelled"


def test_lineage_diagnostic_forces_successful_session_log_flush() -> None:
    result = SubprocessResult(
        0,
        "",
        "",
        TerminationReason.NATURAL_EXIT,
        pid=123,
    )
    assert not diagnostics.should_flush(result, _successful_result(), "", None)
    assert diagnostics.should_flush(result, _successful_result(), "", _diagnostic())
