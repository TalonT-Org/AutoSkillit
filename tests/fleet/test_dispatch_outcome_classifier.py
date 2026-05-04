"""Tests for classify_dispatch_outcome() pure classification function."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import FleetErrorCode, SkillResult
from autoskillit.fleet import DispatchStatus
from autoskillit.fleet._api import classify_dispatch_outcome
from autoskillit.fleet.result_parser import L2ParseResult
from tests.fakes import _DEFAULT_SKILL_RESULT

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _no_sentinel(
    session_id: str = "", lifespan_started: bool = False
) -> tuple[L2ParseResult, SkillResult]:
    parsed = L2ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )
    skill_result = dataclasses.replace(
        _DEFAULT_SKILL_RESULT,
        session_id=session_id,
        lifespan_started=lifespan_started,
    )
    return parsed, skill_result


class TestClassifyDispatchOutcomeNoSentinel:
    def test_no_sentinel_with_session_and_sidecar_is_resumable(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK

    def test_no_sentinel_without_session_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="", lifespan_started=True)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK

    def test_no_sentinel_lifespan_not_started_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=False)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK

    def test_no_sentinel_without_sidecar_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK


class TestClassifyDispatchOutcomeCompletedClean:
    def test_completed_clean_success(self):
        parsed = L2ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.SUCCESS
        assert reason == ""

    def test_completed_clean_failure(self):
        parsed = L2ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": "my-error"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == "my-error"


class TestClassifyDispatchOutcomeCompletedDirty:
    def test_completed_dirty_is_failure(self):
        parsed = L2ParseResult(
            outcome="completed_dirty",
            payload=None,
            raw_body="garbled",
            parse_error="json decode error",
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L2_PARSE_FAILED
