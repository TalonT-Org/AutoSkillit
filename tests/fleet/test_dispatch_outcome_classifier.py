"""Tests for classify_dispatch_outcome() pure classification function."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import (
    FleetErrorCode,
    InfraOutcome,
    RetryReason,
    SessionCheckpoint,
    SkillResult,
)
from autoskillit.fleet import DispatchStatus
from autoskillit.fleet._outcome import classify_dispatch_outcome
from autoskillit.fleet.result_parser import L3ParseResult
from tests.fakes import _DEFAULT_SKILL_RESULT

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _no_sentinel(
    session_id: str = "", lifespan_started: bool = False
) -> tuple[L3ParseResult, SkillResult]:
    parsed = L3ParseResult(
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
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_no_sentinel_without_session_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="", lifespan_started=True)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_no_sentinel_lifespan_not_started_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=False)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_no_sentinel_without_sidecar_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK


class TestClassifyDispatchOutcomeCompletedClean:
    def test_completed_clean_success(self):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(
            parsed,
            skill_result,
            sidecar_exists=False,
            success_reasons=None,
        )
        assert status == DispatchStatus.SUCCESS
        assert reason == ""

    def test_context_exhaustion_overrides_completed_clean_success(self):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            retry_reason=RetryReason.CONTEXT_EXHAUSTED,
            infra=InfraOutcome(exit_category="context_exhausted"),
        )

        status, reason = classify_dispatch_outcome(parsed, skill_result)

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_completed_clean_failure(self):
        parsed = L3ParseResult(
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


class TestQuotaExhaustedOutcome:
    def test_quota_exhausted_is_resumable(self):
        """completed_clean + success=false + reason=fleet_quota_exhausted → RESUMABLE."""
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": FleetErrorCode.FLEET_QUOTA_EXHAUSTED},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_QUOTA_EXHAUSTED


class TestInfrastructureFailureReasons:
    def test_has_failed_dispatch_returns_false_for_quota_exhaustion(self, tmp_path):
        """Quota exhaustion is infrastructure failure — does not halt campaign."""
        from autoskillit.core import FleetErrorCode as FEC
        from autoskillit.fleet.campaign_state.state import _write_state as write_state
        from autoskillit.fleet.campaign_state.state_records import CampaignState, DispatchRecord
        from autoskillit.fleet.campaign_state.state_recovery import has_failed_dispatch

        dispatches = [
            DispatchRecord(
                name="step_1",
                status=DispatchStatus.FAILURE,
                reason=FEC.FLEET_QUOTA_EXHAUSTED,
            ),
        ]
        state = CampaignState(
            schema_version=4,
            campaign_id="test-id",
            campaign_name="test",
            manifest_path="manifest.yaml",
            started_at=0.0,
            dispatches=dispatches,
        )
        write_state(tmp_path / "state.json", state)
        assert not has_failed_dispatch(tmp_path / "state.json")


class TestClassifyDispatchOutcomeCompletedDirty:
    def test_completed_dirty_is_failure(self):
        parsed = L3ParseResult(
            outcome="completed_dirty",
            payload=None,
            raw_body="garbled",
            parse_error="json decode error",
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_PARSE_FAILED


class TestManagedCaptureDiagnosticBoundaries:
    def test_failure_result_text_cannot_change_standard_classification_reason(self):
        diagnostic = (
            "requested_mode=direct effective_mode=capture "
            "reason=invalid_lineage lineage_status=corrupt "
            "launch_id=feedfacefeedfacefeedfacefeedface "
            "attempt_id=deaddeaddeaddeaddeaddeaddeaddead"
        )
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            result=diagnostic,
            stderr=diagnostic,
            success=False,
        )

        status, reason = classify_dispatch_outcome(
            None,
            skill_result,
            sidecar_exists=False,
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
        assert "feedface" not in reason
        assert "deaddead" not in reason

    def test_diagnostic_payload_reason_uses_standard_fleet_failure_wording(self):
        diagnostic = "managed lineage validation failed launch_id=feedfacefeedfacefeedfacefeedface"
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": diagnostic},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT, success=False),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
        assert "feedface" not in reason

    @pytest.mark.parametrize(
        "private_record",
        [
            ".autoskillit/managed-headless-session-lineage/",
            "/tmp/project/.autoskillit/managed-headless-session-lineage/records/"
            "0123456789abcdef0123456789abcdef.json",
        ],
    )
    def test_failed_reason_preserves_uncommitted_changes_cause(self, private_record):
        diagnostic = (
            f"uncommitted_changes: bootstrap_clone failed at {private_record}; "
            "cache path /tmp/project/cache/clone.lock"
        )
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": diagnostic},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT, success=False),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == (
            "uncommitted_changes: bootstrap_clone failed at <redacted>; "
            "cache path /tmp/project/cache/clone.lock"
        )
        assert private_record not in reason

    def test_failed_reason_redacts_inline_capture_fields_but_keeps_cause(self):
        private_record = (
            "/tmp/project/.autoskillit/managed-headless-session-lineage/records/"
            "abcdef0123456789abcdef0123456789.json"
        )
        diagnostic = (
            f"bootstrap_clone failed at {private_record} "
            "attempt_id=deaddeaddeaddeaddeaddeaddeaddead "
            "launch_id=feedfacefeedfacefeedfacefeedface "
            "requested_mode=direct effective_mode=capture "
            "lineage_status=corrupt native_shell_capture=enabled"
        )
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": diagnostic},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT, success=False),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == (
            "bootstrap_clone failed at <redacted> <redacted> <redacted> "
            "<redacted> <redacted> <redacted> <redacted>"
        )
        for private_value in (
            private_record,
            "deaddead",
            "feedface",
            "direct",
            "capture",
            "corrupt",
            "enabled",
        ):
            assert private_value not in reason

    @pytest.mark.parametrize(
        "private_record",
        [
            ".autoskillit/managed-headless-session-lineage/",
            "/tmp/project/.autoskillit/managed-headless-session-lineage/records/"
            "0123456789abcdef0123456789abcdef.json",
        ],
    )
    def test_private_path_only_uses_standard_startup_failure(self, private_record):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": private_record},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT, success=False),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH


class TestReasonAwareResumePolicy:
    def test_idle_stall_with_progress_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="idle_stall",
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_context_exhausted_with_progress_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="resume",
            infra=InfraOutcome(exit_category="context_exhausted"),
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_api_error_with_progress_is_resumable(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="resume",
            infra=InfraOutcome(exit_category="api_error"),
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_process_killed_with_progress_is_resumable(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="resume",
            infra=InfraOutcome(exit_category="process_killed"),
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_thinking_stall_with_progress_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="thinking_stall",
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_stale_with_progress_is_failure(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="stale",
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_no_progress_still_failure_regardless_of_reason(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="idle_stall",
        )
        status, reason = classify_dispatch_outcome(
            parsed, skill_result, sidecar_exists=False, checkpoint=None
        )
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_no_lifespan_still_failure_regardless_of_reason(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=False)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="idle_stall",
        )
        status, reason = classify_dispatch_outcome(parsed, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK


class TestClassifyDispatchOutcomeSidecarSynthesis:
    def test_sidecar_source_accepted_in_l3_parse_result(self):
        """L3ParseResult with source='sidecar' must be constructible."""
        result = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": "sidecar_recovery"},
            raw_body=None,
            parse_error=None,
            source="sidecar",
        )
        assert result.source == "sidecar"

    def test_sidecar_synthesized_completed_clean_is_success(self):
        """The reserved sidecar reason is accepted outside recipe declarations."""
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": "sidecar_recovery"},
            raw_body=None,
            parse_error=None,
            source="sidecar",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(
            parsed,
            skill_result,
            success_reasons=frozenset({"implementation_complete"}),
        )
        assert status == DispatchStatus.SUCCESS
        assert reason == "sidecar_recovery"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"success": True}, id="missing"),
            pytest.param({"success": True, "reason": ""}, id="empty"),
            pytest.param({"success": True, "reason": "other_recovery"}, id="different"),
        ],
    )
    def test_reasonless_recipe_sidecar_requires_exact_reserved_reason(self, payload):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload=payload,
            raw_body=None,
            parse_error=None,
            source="sidecar",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT),
            success_reasons=None,
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_RESULT_CONTRACT_VIOLATION


class TestCompletedCleanSuccessPreservesReason:
    def test_completed_clean_success_preserves_reason(self):
        """A wholly reasonless recipe keeps a supplied success reason."""
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": "implementation_complete"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(
            parsed,
            skill_result,
            success_reasons=None,
        )
        assert status == DispatchStatus.SUCCESS
        assert reason == "implementation_complete"

    def test_completed_clean_success_distinct_reasons_distinguishable(self):
        """Distinct reason strings are preserved through classification."""
        reasons = ["implementation_complete", "no_changes", "already_done"]
        for expected_reason in reasons:
            parsed = L3ParseResult(
                outcome="completed_clean",
                payload={"success": True, "reason": expected_reason},
                raw_body=None,
                parse_error=None,
                source="stdout",
            )
            skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
            status, reason = classify_dispatch_outcome(
                parsed,
                skill_result,
                success_reasons=None,
            )
            assert status == DispatchStatus.SUCCESS
            assert reason == expected_reason

    def test_completed_clean_success_empty_reason_returns_empty(self):
        """Wholly reasonless recipes retain the supplied empty-reason behavior."""
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        skill_result = dataclasses.replace(_DEFAULT_SKILL_RESULT)
        status, reason = classify_dispatch_outcome(
            parsed,
            skill_result,
            success_reasons=None,
        )
        assert status == DispatchStatus.SUCCESS
        assert reason == ""


class TestDeclaredSuccessReasonContract:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"success": True}, id="missing"),
            pytest.param({"success": True, "reason": ""}, id="empty"),
            pytest.param({"success": True, "reason": "already_done"}, id="undeclared"),
            pytest.param(
                {"success": True, "reason": " implementation_complete "},
                id="whitespace-is-not-trimmed-for-admission",
            ),
        ],
    )
    def test_declared_recipe_rejects_missing_empty_or_nonexact_reason(self, payload):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload=payload,
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT),
            success_reasons=frozenset({"implementation_complete"}),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_RESULT_CONTRACT_VIOLATION

    def test_declared_reason_is_accepted_exactly(self):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": "implementation_complete"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT),
            success_reasons=frozenset({"implementation_complete"}),
        )

        assert status == DispatchStatus.SUCCESS
        assert reason == "implementation_complete"

    def test_failure_payload_is_not_subject_to_success_reason_admission(self):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": False, "reason": "implementation_complete"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT),
            success_reasons=frozenset({"implementation_complete"}),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == "implementation_complete"

    def test_stdout_cannot_claim_the_sidecar_reserved_reason(self):
        parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": "sidecar_recovery"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

        status, reason = classify_dispatch_outcome(
            parsed,
            dataclasses.replace(_DEFAULT_SKILL_RESULT),
            success_reasons=frozenset({"implementation_complete"}),
        )

        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_RESULT_CONTRACT_VIOLATION

    def test_every_bundled_declared_reason_is_admitted_from_fresh_recipe_data(self):
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        checked_reasons = 0
        for recipe_path in sorted(builtin_recipes_dir().glob("*.yaml")):
            recipe = load_recipe(recipe_path)
            declared_reasons = recipe.declared_success_reasons()
            if declared_reasons is None:
                continue

            for declared_reason in declared_reasons:
                parsed = L3ParseResult(
                    outcome="completed_clean",
                    payload={"success": True, "reason": declared_reason},
                    raw_body=None,
                    parse_error=None,
                    source="stdout",
                )
                status, reason = classify_dispatch_outcome(
                    parsed,
                    dataclasses.replace(_DEFAULT_SKILL_RESULT),
                    success_reasons=declared_reasons,
                )

                assert status == DispatchStatus.SUCCESS, recipe_path.name
                assert reason == declared_reason, recipe_path.name
                checked_reasons += 1

        assert checked_reasons > 0


class TestClassifyDispatchOutcomeParsedNone:
    def test_parsed_none_with_progress_is_resumable(self):
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="sess-abc",
            lifespan_started=True,
        )
        status, reason = classify_dispatch_outcome(None, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_parsed_none_with_abandon_reason_is_failure(self):
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="sess-abc",
            lifespan_started=True,
            retry_reason="idle_stall",
        )
        status, reason = classify_dispatch_outcome(None, skill_result, sidecar_exists=True)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_parsed_none_without_progress_is_failure(self):
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="",
        )
        status, reason = classify_dispatch_outcome(None, skill_result, sidecar_exists=False)
        assert status == DispatchStatus.FAILURE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK


class TestCheckpointOnlyResumePolicy:
    def test_process_killed_no_sidecar_with_checkpoint_is_resumable(self):
        parsed, skill_result = _no_sentinel(session_id="sess-abc", lifespan_started=True)
        skill_result = dataclasses.replace(
            skill_result,
            retry_reason="resume",
            infra=InfraOutcome(exit_category="process_killed"),
        )
        checkpoint = SessionCheckpoint(
            completed_items=["plan", "verify", "implement"],
            step_name="implement",
            progress_pct=0.375,
            ts="2026-06-01T00:00:00Z",
        )
        status, reason = classify_dispatch_outcome(
            parsed, skill_result, sidecar_exists=False, checkpoint=checkpoint
        )
        assert status == DispatchStatus.RESUMABLE
        assert reason == FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    def test_timeout_no_sidecar_with_checkpoint_is_resumable(self):
        skill_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="sess-abc",
            lifespan_started=True,
            subtype="timeout",
            retry_reason="resume",
            infra=InfraOutcome(exit_category="process_killed"),
        )
        checkpoint = SessionCheckpoint(
            completed_items=["plan"],
            step_name="plan",
            progress_pct=0.125,
            ts="2026-06-01T00:00:00Z",
        )
        status, reason = classify_dispatch_outcome(
            None, skill_result, sidecar_exists=False, checkpoint=checkpoint, subtype="timeout"
        )
        assert status == DispatchStatus.RESUMABLE
