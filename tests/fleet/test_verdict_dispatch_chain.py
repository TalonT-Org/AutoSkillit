"""Integration tests for verdict → sidecar synthesis → dispatch status chain."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.fleet import DispatchStatus
from autoskillit.fleet._outcome import classify_dispatch_outcome
from autoskillit.fleet._sidecar_synthesis import synthesize_from_sidecar
from autoskillit.fleet.result_parser import L3ParseResult
from autoskillit.fleet.sidecar import IssueSidecarEntry
from autoskillit.fleet.state_types import _ALLOWED_TRANSITIONS
from tests.fakes import _DEFAULT_SKILL_RESULT

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

URL = "https://github.com/org/repo/issues/1"
TS = "2026-06-01T00:00:00Z"
PR = "https://github.com/org/repo/pull/1"


class TestVerdictDispatchChain:
    def test_completed_clean_failure_produces_failure_status(self) -> None:
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": "ci_only_failure"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result)
        assert status == DispatchStatus.FAILURE
        assert reason == "ci_only_failure"

    def test_sidecar_does_not_override_completed_clean_failure(self) -> None:
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        entries = [
            IssueSidecarEntry(
                issue_url=URL, status="completed", ts=TS, pr_url=PR, terminal_step="done"
            )
        ]
        result = synthesize_from_sidecar(parsed, entries, 1)
        assert result is parsed

    def test_no_sentinel_with_failure_terminal_sidecar_blocked(self) -> None:
        parsed = L3ParseResult(
            outcome="no_sentinel",
            payload=None,
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        entries = [
            IssueSidecarEntry(
                issue_url=URL,
                status="completed",
                ts=TS,
                pr_url=PR,
                terminal_step="escalate_stop",
            )
        ]
        result = synthesize_from_sidecar(parsed, entries, 1)
        assert result is parsed

    def test_no_sentinel_with_success_terminal_sidecar_synthesized(self) -> None:
        parsed = L3ParseResult(
            outcome="no_sentinel",
            payload=None,
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        entries = [
            IssueSidecarEntry(
                issue_url=URL, status="completed", ts=TS, pr_url=PR, terminal_step="done"
            )
        ]
        result = synthesize_from_sidecar(parsed, entries, 1)
        assert result is not parsed
        assert result.outcome == "completed_clean"
        assert result.payload is not None
        assert result.payload["success"] is True

    def test_failure_to_success_transition_forbidden(self) -> None:
        allowed = _ALLOWED_TRANSITIONS[DispatchStatus.FAILURE]
        assert DispatchStatus.SUCCESS not in allowed
