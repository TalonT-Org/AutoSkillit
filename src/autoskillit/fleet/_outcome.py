"""Dispatch outcome classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import regex as re

from autoskillit.core import (
    FleetErrorCode,
    InfraExitCategory,
    RetryReason,
    SessionCheckpoint,
    SkillResult,
    truncate_text,
)
from autoskillit.fleet._sidecar_synthesis import SIDECAR_RECOVERY_REASON
from autoskillit.fleet.campaign_state.state import (
    DispatchRecord,
    DispatchStateHandle,
    DispatchStatus,
    normalize_dispatch_token_usage,
)
from autoskillit.fleet.campaign_state.state_effects import DispatchEffectProvenance
from autoskillit.fleet.campaign_state.state_outcomes import DispatchCompleted, DispatchResult
from autoskillit.fleet.campaign_state.state_transitions import (
    _ABANDON_REASONS,
    is_context_exhausted,
)
from autoskillit.fleet.result_parser import L3ParseResult

ENVELOPE_STDERR_MAX = 2000


class _DispatchCommonFields(TypedDict):
    dispatch_id: str
    dispatched_session_id: str
    diagnostic_message: str
    token_usage: dict[str, Any]
    lifespan_started: bool
    stderr: str
    elapsed_seconds: float
    effect_provenance: DispatchEffectProvenance


_MANAGED_CAPTURE_DIAGNOSTIC_MARKERS: tuple[str, ...] = (
    "native_shell_capture",
    "native shell capture",
    "managed-headless-session-lineage",
    "managed headless session lineage",
    "managed lineage",
    "lineage_status",
    "lineage validation",
    "launch_id",
    "attempt_id",
)

_MANAGED_LINEAGE_PATH_RE = re.compile(
    r"(?<![\w./-])(?:(?:/(?:[^/\\\r\n\"'<>|,;:(){}\[\]]+/)*)|"
    r"(?:[^/\s\\\r\n\"'<>|,;:(){}\[\]]+/)+)?"
    r"\.autoskillit/managed-headless-session-lineage"
    r"(?=$|[/\s\"'<>|,;:(){}\[\]])(?:/[^\\\s\"'<>|,;:(){}\[\]]*)?",
    re.IGNORECASE,
)
_MANAGED_CAPTURE_FIELD_ASSIGNMENT_RE = re.compile(
    r"(?<![\w./-])(?P<quote>[\"']?)"
    r"(?:attempt_id|launch_id|requested_mode|effective_mode|lineage[-_ ]status|"
    r"native[-_ ]shell[-_ ]capture|managed[-_ ]headless[-_ ]session[-_ ]lineage|"
    r"managed[-_ ]lineage|lineage[-_ ]validation)"
    r"(?P=quote)\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_REDACTED_ONLY_LINE_RE = re.compile(r"(?:<redacted>|[\s\p{P}])*", re.IGNORECASE)


def _is_managed_capture_diagnostic_text(value: str) -> bool:
    """Return whether text belongs only in managed-lineage diagnostics."""
    normalized = value.casefold()
    return any(marker in normalized for marker in _MANAGED_CAPTURE_DIAGNOSTIC_MARKERS) or (
        "requested_mode" in normalized and "effective_mode" in normalized
    )


def _sanitize_managed_capture_diagnostics(value: str) -> str:
    """Remove managed-capture diagnostic lines from fleet-facing text."""
    return "".join(
        line
        for line in value.splitlines(keepends=True)
        if not _is_managed_capture_diagnostic_text(line)
    )


def success_reason_contract_error(
    reason: object,
    success_reasons: frozenset[str] | None,
) -> str | None:
    """Return a sanitized diagnostic when a success reason violates declarations."""
    if success_reasons is None:
        return None
    if not isinstance(reason, str):
        return "Dispatch success reason must be a string."
    if not reason:
        return "Dispatch success is missing its declared reason."
    if reason not in success_reasons:
        sanitized_reason = _sanitize_managed_capture_diagnostics(reason)
        if sanitized_reason:
            return f"Dispatch success reason {sanitized_reason!r} is not declared."
        return "Dispatch success reason is not declared."
    return None


def _sanitize_failed_managed_capture_reason(value: str) -> str:
    """Redact private lineage details while retaining failure causes."""
    normalized = _MANAGED_LINEAGE_PATH_RE.sub("<redacted>", value)
    normalized = _MANAGED_CAPTURE_FIELD_ASSIGNMENT_RE.sub("<redacted>", normalized)
    return "".join(
        line
        for line in normalized.splitlines(keepends=True)
        if not _REDACTED_ONLY_LINE_RE.fullmatch(line)
    )


def _is_abandon_reason(skill_result: SkillResult) -> bool:
    """Return True when the kill reason indicates resume would be futile."""
    if skill_result.retry_reason in _ABANDON_REASONS:
        return True
    if (
        skill_result.retry_reason == RetryReason.RESUME
        and skill_result.infra.exit_category == InfraExitCategory.CONTEXT_EXHAUSTED
    ):
        return True
    return False


def _checkpoint_to_dict(cp: SessionCheckpoint | None) -> dict[str, Any]:
    """Convert SessionCheckpoint to a plain dict for DispatchRecord storage."""
    if cp is None:
        return {}
    return cp.to_dict()


def _classify_missing_result(
    skill_result: SkillResult,
    *,
    sidecar_exists: bool,
    checkpoint: SessionCheckpoint | None,
    error_code: FleetErrorCode,
) -> tuple[DispatchStatus, str]:
    """Classify a missing result according to resumability signals."""
    has_progress = checkpoint is not None or sidecar_exists
    if skill_result.session_id and skill_result.lifespan_started and has_progress:
        if _is_abandon_reason(skill_result):
            return DispatchStatus.FAILURE, error_code
        return DispatchStatus.RESUMABLE, error_code
    return DispatchStatus.FAILURE, error_code


def _classify_clean_result(
    parsed: L3ParseResult,
    *,
    success_reasons: frozenset[str] | None,
) -> tuple[DispatchStatus, str]:
    """Classify a decoded result block, including success-reason enforcement."""
    payload = parsed.payload
    if payload and payload.get("success") is True:
        raw_reason = payload.get("reason", "")
        if parsed.source == "sidecar":
            if raw_reason != SIDECAR_RECOVERY_REASON:
                return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_RESULT_CONTRACT_VIOLATION
            return DispatchStatus.SUCCESS, _sanitize_managed_capture_diagnostics(raw_reason)
        if success_reason_contract_error(raw_reason, success_reasons) is not None:
            return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_RESULT_CONTRACT_VIOLATION
        return DispatchStatus.SUCCESS, _sanitize_managed_capture_diagnostics(str(raw_reason))
    if payload and payload.get("reason", "") == FleetErrorCode.FLEET_QUOTA_EXHAUSTED:
        return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_QUOTA_EXHAUSTED

    raw_reason = str(payload.get("reason", "")) if payload else ""
    reason = _sanitize_managed_capture_diagnostics(
        _sanitize_failed_managed_capture_reason(raw_reason)
    )
    if raw_reason and not reason:
        reason = FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
    return DispatchStatus.FAILURE, reason


def classify_dispatch_outcome(
    parsed: L3ParseResult | None,
    skill_result: SkillResult,
    *,
    sidecar_exists: bool = False,
    checkpoint: SessionCheckpoint | None = None,
    subtype: str = "",
    success_reasons: frozenset[str] | None = None,
    context_compacted: bool = False,
) -> tuple[DispatchStatus, str]:
    """Map L2 food truck subprocess signals to a (DispatchStatus, reason) pair.

    Pure function — no filesystem access, no side effects.
    Apply these ordered rules:
      0. context exhaustion or compaction attempt → terminal context FAILURE
      1. timeout + session_id + lifespan_started + (checkpoint or sidecar)
         + not abandon → RESUMABLE
      2. timeout (any other case) → FAILURE
      3. completed_clean + true success + valid reason contract → SUCCESS
      4. completed_clean + no success → FAILURE
      5. completed_dirty → FAILURE (fleet_l3_parse_failed)
      6. no_sentinel + session_id + lifespan_started + (checkpoint or sidecar)
         + not abandon → RESUMABLE
      7. no_sentinel (any other case) → FAILURE (fleet_l3_no_result_block)
    """
    if context_compacted or is_context_exhausted(
        skill_result.retry_reason or "",
        skill_result.infra.exit_category or "",
        skill_result.compaction_attempted,
    ):
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_CONTEXT_EXHAUSTED

    if subtype == "timeout":
        return _classify_missing_result(
            skill_result,
            sidecar_exists=sidecar_exists,
            checkpoint=checkpoint,
            error_code=FleetErrorCode.FLEET_L3_TIMEOUT,
        )

    if parsed is None:
        return _classify_missing_result(
            skill_result,
            sidecar_exists=sidecar_exists,
            checkpoint=checkpoint,
            error_code=FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
        )

    if parsed.outcome == "completed_clean":
        return _classify_clean_result(
            parsed,
            success_reasons=success_reasons,
        )
    if parsed.outcome == "completed_dirty":
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_PARSE_FAILED
    return _classify_missing_result(
        skill_result,
        sidecar_exists=sidecar_exists,
        checkpoint=checkpoint,
        error_code=FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
    )


def build_dispatch_result(
    *,
    parsed_result: L3ParseResult | None,
    final_status: DispatchStatus,
    reason: str,
    dispatch_id: str,
    skill_result: SkillResult,
    dispatched_session_id: str,
    backend_name: str,
    dispatch_checkpoint: SessionCheckpoint | None,
    started_at: float,
    ended_at: float,
    state_path: Path,
    effect_provenance: DispatchEffectProvenance,
    diagnostic_message: str = "",
) -> DispatchResult:
    """Build the bounded fleet envelope without exposing managed diagnostics."""
    common: _DispatchCommonFields = {
        "dispatch_id": dispatch_id,
        "dispatched_session_id": dispatched_session_id,
        "diagnostic_message": diagnostic_message,
        "token_usage": normalize_dispatch_token_usage(
            skill_result.token_usage or {},
            backend=backend_name,
            provider_used=skill_result.provider.provider_used,
        ),
        "lifespan_started": skill_result.lifespan_started,
        "stderr": truncate_text(
            _sanitize_managed_capture_diagnostics(skill_result.stderr or ""),
            ENVELOPE_STDERR_MAX,
        ),
        "elapsed_seconds": ended_at - started_at,
        "effect_provenance": effect_provenance,
    }
    if parsed_result is not None and parsed_result.outcome == "completed_clean":
        return DispatchResult(
            DispatchCompleted(
                dispatch_status=final_status,
                reason=reason,
                l3_payload=parsed_result.payload,
                l3_parse_source=parsed_result.source,
                **common,
            ),
            per_dispatch_state_path=state_path,
        )
    if parsed_result is not None and parsed_result.outcome == "completed_dirty":
        return DispatchResult(
            DispatchCompleted(
                dispatch_status=final_status,
                reason=reason,
                l3_payload=None,
                l3_raw_body=parsed_result.raw_body,
                l3_parse_error=parsed_result.parse_error,
                l3_parse_source=parsed_result.source,
                **common,
            ),
            per_dispatch_state_path=state_path,
        )
    return DispatchResult(
        DispatchCompleted(
            dispatch_status=final_status,
            reason=reason,
            l3_payload=None,
            l3_parse_source=(parsed_result.source if parsed_result is not None else "stdout"),
            resume_checkpoint=_checkpoint_to_dict(dispatch_checkpoint),
            **common,
        ),
        per_dispatch_state_path=state_path,
    )


def build_success_short_circuit(
    record: DispatchRecord,
    handle: DispatchStateHandle,
    effect_provenance: DispatchEffectProvenance,
) -> DispatchResult:
    """Mirror a prior succeeded dispatch without launching a new subprocess."""
    return DispatchResult(
        outcome=DispatchCompleted(
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id=record.dispatch_id,
            dispatched_session_id=record.dispatched_session_id,
            reason=record.reason,
            effect_provenance=effect_provenance,
            token_usage=dict(record.token_usage),
        ),
        per_dispatch_state_path=handle.state_path,
    )
