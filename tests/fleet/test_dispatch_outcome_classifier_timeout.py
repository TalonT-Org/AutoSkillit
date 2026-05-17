"""Tests for classify_dispatch_outcome() with timeout inputs — the RESUMABLE gap."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import FleetErrorCode, InfraOutcome, RetryReason, SkillResult
from autoskillit.fleet import DispatchStatus
from autoskillit.fleet._api import classify_dispatch_outcome
from autoskillit.fleet.result_parser import L3ParseResult
from tests.fakes import _DEFAULT_SKILL_RESULT

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _no_sentinel(
    session_id: str = "",
    lifespan_started: bool = False,
    retry_reason: RetryReason = RetryReason.NONE,
    infra_exit_category: str = "",
) -> tuple[L3ParseResult, SkillResult]:
    parsed = L3ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )
    skill_result_kwargs: dict = dict(
        session_id=session_id,
        lifespan_started=lifespan_started,
        retry_reason=retry_reason,
    )
    if infra_exit_category:
        skill_result_kwargs["infra"] = InfraOutcome(exit_category=infra_exit_category)
    skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT, **skill_result_kwargs)
    return parsed, skill_result


def _timeout_result(
    session_id: str = "",
    lifespan_started: bool = False,
    retry_reason: RetryReason = RetryReason.NONE,
    infra_exit_category: str = "",
) -> SkillResult:
    kwargs: dict = dict(
        session_id=session_id,
        lifespan_started=lifespan_started,
        subtype="timeout",
        success=False,
        retry_reason=retry_reason,
    )
    if infra_exit_category:
        kwargs["infra"] = InfraOutcome(exit_category=infra_exit_category)
    return dataclasses.replace(_DEFAULT_SKILL_RESULT, **kwargs)


class TestTimeoutWithResumableConditions:
    def test_timeout_with_session_and_sidecar_is_resumable(self):
        """A timed-out session with session_id + lifespan_started + sidecar → RESUMABLE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_TIMEOUT

    def test_timeout_with_session_and_checkpoint_is_resumable(self):
        """A timed-out session with session_id + lifespan_started + checkpoint → RESUMABLE."""
        from autoskillit.core.types import SessionCheckpoint

        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
        )
        checkpoint = SessionCheckpoint(
            completed_items=["item-1"],
            step_name="test-step",
            progress_pct=0.5,
            ts="2026-01-01T00:00:00Z",
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=False,
            checkpoint=checkpoint,
            subtype="timeout",
        )
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_TIMEOUT

    def test_timeout_without_session_is_failure(self):
        """A timed-out session without session_id → FAILURE (no evidence of progress)."""
        skill_result = _timeout_result(session_id="", lifespan_started=True)
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_TIMEOUT

    def test_timeout_without_lifespan_started_is_failure(self):
        """A timed-out session with session_id but no lifespan → FAILURE."""
        skill_result = _timeout_result(session_id="sess-abc", lifespan_started=False)
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_TIMEOUT

    def test_timeout_without_sidecar_or_checkpoint_is_failure(self):
        """A timed-out session with session_id + lifespan but no checkpoint/sidecar → FAILURE."""
        skill_result = _timeout_result(session_id="sess-abc", lifespan_started=True)
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=False,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_TIMEOUT


class TestTimeoutWithAbandonReasons:
    def test_idle_stall_timeout_is_failure(self):
        """Timeout + idle_stall abandon reason → FAILURE even with session + sidecar."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.IDLE_STALL,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE

    def test_thinking_stall_timeout_is_failure(self):
        """Timeout + thinking_stall abandon reason → FAILURE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.THINKING_STALL,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE

    def test_path_contamination_timeout_is_failure(self):
        """Timeout + path_contamination abandon reason → FAILURE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.PATH_CONTAMINATION,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE

    def test_clone_contamination_timeout_is_failure(self):
        """Timeout + clone_contamination abandon reason → FAILURE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.CLONE_CONTAMINATION,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE

    def test_stale_timeout_is_failure(self):
        """Timeout + stale abandon reason → FAILURE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.STALE,
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE


class TestTimeoutContextExhaustedIsFailure:
    def test_timeout_context_exhausted_is_failure(self):
        """Timeout + context_exhausted (abandon via infra category) → FAILURE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.RESUME,
            infra_exit_category="context_exhausted",
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.FAILURE

    def test_timeout_api_error_is_resumable(self):
        """Timeout + api_error infra category → RESUMABLE (infrastructure, not abandon)."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.RESUME,
            infra_exit_category="api_error",
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.RESUMABLE

    def test_timeout_process_killed_is_resumable(self):
        """Timeout + process_killed infra category → RESUMABLE."""
        skill_result = _timeout_result(
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason=RetryReason.RESUME,
            infra_exit_category="process_killed",
        )
        status, reason = classify_dispatch_outcome(
            parsed=None,  # type: ignore[arg-type]
            skill_result=skill_result,
            sidecar_exists=True,
            checkpoint=None,
            subtype="timeout",
        )
        assert status == DispatchStatus.RESUMABLE
