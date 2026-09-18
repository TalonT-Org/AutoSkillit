"""Keep the live SkillResult JSON projection typed at the MCP boundary."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from typing import get_type_hints

import pytest

from autoskillit.core import (
    AdjudicationVerdict,
    ApiFailureOutcome,
    ApiRetryOutcome,
    AuditAttemptId,
    AuditOutcomeStatus,
    AuditResultOutcome,
    AuditVerdict,
    ContaminationOutcome,
    FaultDomain,
    InfraOutcome,
    KillReason,
    NdjsonDriftOutcome,
    ProviderOutcome,
    RateLimitWindow,
    RetryReason,
    SkillResult,
    WriteEvidence,
)
from autoskillit.server.tools._types import RunSkillResult

pytestmark = pytest.mark.small

_SERVER_ENVELOPE_ONLY = {
    "error",
    "pipeline_tracker",
    "receipt_id",
    "recipe_segment",
    "retriable",
    "stage",
}


# These SkillResult fields deliberately do not retain their dataclass field name in
# the wire envelope. Keep every departure from direct serialization explicit so a
# new field cannot disappear from run_skill silently.
_ENVELOPE_EXCLUDED_FIELDS = {
    "api_failure": "flattened into api_error_*, api_terminal_reason, and rate_limit_* keys",
    "api_retry": "flattened into api_retry_* keys",
    "audit": "flattened into audit_* keys",
    "contamination": "flattened into pre_contamination_* keys",
    "evidence": "flattened into write and progress-evidence keys",
    "infra": "flattened into infra_* keys",
    "ndjson_drift": "flattened into ndjson_* keys",
    "provider": "flattened into provider_* keys",
    "turn_usage": "per-turn telemetry is not part of the run_skill envelope",
}

_FLATTENED_ENVELOPE_FIELDS = {
    "api_failure": {
        "api_error_code": "server_error",
        "api_error_message_seen": True,
        "api_error_status": 503,
        "api_terminal_reason": "api_error",
        "rate_limit_resets_at_epoch": 2_000_000_000,
        "rate_limit_status": "rejected",
        "rate_limit_type": "seven_day",
    },
    "api_retry": {
        "api_retry_count": 1,
        "api_retry_exhausted": True,
        "api_retry_last_error": "retry",
        "api_retry_last_status": 503,
    },
    "audit": {
        "audit_attempt_id": "attempt-1",
        "audit_cycle_path": "cycle/1",
        "audit_status": AuditOutcomeStatus.PUBLISHED.value,
        "audit_verdict": AuditVerdict.GO.value,
    },
    "contamination": {
        "pre_contamination_retry_reason": RetryReason.CLONE_CONTAMINATION.value,
        "pre_contamination_subtype": "contaminated",
    },
    "evidence": {
        "file_changes_count": 1,
        "fs_writes_detected": True,
        "git_writes_detected": True,
        "has_implementation_progress": True,
        "has_progress_evidence": True,
        "write_call_count": 1,
    },
    "infra": {
        "infra_cleanup_incomplete": True,
        "infra_exit_category": "api_error",
        "infra_fault_domain": FaultDomain.INFRASTRUCTURE.value,
    },
    "ndjson_drift": {
        "ndjson_unknown_event_count": 1,
        "ndjson_unknown_item_count": 2,
    },
    "provider": {
        "provider_fallback": True,
        "provider_used": "anthropic",
    },
}


def _populated_skill_result() -> SkillResult:
    return SkillResult(
        success=False,
        result="payload",
        session_id="session",
        subtype="api_error",
        is_error=True,
        exit_code=1,
        needs_retry=True,
        retry_reason=RetryReason.RESUME,
        stderr="stderr",
        token_usage={"input_tokens": 1},
        turn_usage=[
            {
                "backend": "claude-code",
                "message_id": "message",
                "request_id": "request",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "model": "model",
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_tokens": None,
                "cache_creation_tokens": None,
                "context_window_tokens": None,
                "context_fraction": None,
            }
        ],
        worktree_path="/tmp/worktree",
        branch_name="branch",
        evidence=WriteEvidence(1, True, True, 1),
        kill_reason=KillReason.INFRA_KILL,
        provider=ProviderOutcome("anthropic", True),
        infra=InfraOutcome("api_error", True, FaultDomain.INFRASTRUCTURE),
        api_retry=ApiRetryOutcome(1, "retry", 503, True),
        api_failure=ApiFailureOutcome(
            status=503,
            terminal_reason="api_error",
            error_code="server_error",
            api_error_message_seen=True,
            rate_limit=RateLimitWindow("rejected", "seven_day", 2_000_000_000),
        ),
        contamination=ContaminationOutcome(RetryReason.CLONE_CONTAMINATION, "contaminated"),
        ndjson_drift=NdjsonDriftOutcome(1, 2),
        audit=AuditResultOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            verdict=AuditVerdict.GO,
            cycle_path="cycle/1",
            attempt_id=AuditAttemptId("attempt-1"),
        ),
        completion_required=True,
        outcome_fields={"attempt": 1},
        outcome_invariant_violated=True,
        outcome_qualifier="retry",
        adjudication_verdict=AdjudicationVerdict(
            reason_kind=RetryReason.OUTCOME_INVARIANT,
            subtype="outcome_invariant_violation",
            detail="distinct adjudication detail",
            outcome_fields={"attempt": 1},
            defects=("distinct defect",),
        ),
    )


def test_run_skill_result_covers_skill_result_projection_bidirectionally() -> None:
    result = _populated_skill_result()
    projection_keys = set(json.loads(result.to_json()))
    typed_keys = set(RunSkillResult.__required_keys__) | set(RunSkillResult.__optional_keys__)

    assert projection_keys <= typed_keys
    assert typed_keys - projection_keys == _SERVER_ENVELOPE_ONLY

    hints = get_type_hints(RunSkillResult)
    assert hints["adjudication_verdict"] == dict[str, object] | None
    assert hints["outcome_fields"] == dict[str, int | str] | None
    assert hints["outcome_invariant_violated"] is bool
    assert hints["outcome_qualifier"] == str | None


@pytest.mark.parametrize(
    ("reason_kind", "subtype", "outcome_fields"),
    [
        (
            RetryReason.OUTCOME_INVARIANT,
            "outcome_invariant_violation",
            {"write_call_count": 0},
        ),
        (
            RetryReason.OUTCOME_REPORT_MALFORMED,
            "outcome_report_malformed",
            {"verdict": "unrecognized"},
        ),
        (
            RetryReason.OUTCOME_INVARIANT,
            "test_evidence_missing",
            {"test_check_count": 0},
        ),
    ],
    ids=["invariant", "malformed-report", "test-gate"],
)
def test_run_skill_result_envelope_carries_causal_adjudication_fields(
    reason_kind: RetryReason,
    subtype: str,
    outcome_fields: dict[str, int | str],
) -> None:
    detail = f"distinct {subtype} detail"
    result = replace(
        _populated_skill_result(),
        result=detail,
        subtype=subtype,
        retry_reason=reason_kind,
        outcome_fields=outcome_fields,
        adjudication_verdict=AdjudicationVerdict(
            reason_kind=reason_kind,
            subtype=subtype,
            detail=detail,
            outcome_fields=outcome_fields,
            defects=("distinct defect",),
        ),
    )

    payload = json.loads(result.to_json())

    assert payload["outcome_fields"] == outcome_fields
    assert payload["adjudication_verdict"] == {
        "reason_kind": reason_kind.value,
        "subtype": subtype,
        "detail": detail,
        "outcome_fields": outcome_fields,
        "defects": ["distinct defect"],
    }


@pytest.mark.parametrize(
    ("reason_kind", "subtype"),
    [
        (RetryReason.CONTRACT_RECOVERY, "artifact_contract_violation"),
        (RetryReason.RESUME, "artifact_adjudication_error"),
    ],
    ids=["contract-violation", "validation-error"],
)
def test_declared_artifact_envelope_does_not_expose_artifact_paths(
    reason_kind: RetryReason,
    subtype: str,
) -> None:
    detail = "safe artifact detail"
    result = replace(
        _populated_skill_result(),
        result=detail,
        subtype=subtype,
        retry_reason=reason_kind,
        outcome_fields=None,
        adjudication_verdict=AdjudicationVerdict(
            reason_kind=reason_kind,
            subtype=subtype,
            detail=detail,
            outcome_fields=None,
            defects=(),
        ),
    )

    payload = json.loads(result.to_json())

    assert payload["result"] == detail
    assert payload["outcome_fields"] is None
    assert payload["adjudication_verdict"]["outcome_fields"] is None


def test_skill_result_envelope_classifies_every_dataclass_field() -> None:
    payload = json.loads(_populated_skill_result().to_json())
    skill_result_fields = {field.name for field in fields(SkillResult)}
    directly_serialized = skill_result_fields & set(payload)

    assert skill_result_fields == directly_serialized | set(_ENVELOPE_EXCLUDED_FIELDS)
    assert not directly_serialized & set(_ENVELOPE_EXCLUDED_FIELDS)
    assert all(_ENVELOPE_EXCLUDED_FIELDS.values())
    assert set(_FLATTENED_ENVELOPE_FIELDS) <= set(_ENVELOPE_EXCLUDED_FIELDS)
    for source_field, expected_values in _FLATTENED_ENVELOPE_FIELDS.items():
        assert source_field in _ENVELOPE_EXCLUDED_FIELDS
        for wire_key, expected_value in expected_values.items():
            assert payload[wire_key] == expected_value
