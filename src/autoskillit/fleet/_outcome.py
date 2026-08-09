"""Dispatch outcome classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from autoskillit.core import (
    FleetErrorCode,
    InfraExitCategory,
    RetryReason,
    SessionCheckpoint,
    SkillResult,
    truncate_text,
)
from autoskillit.fleet.result_parser import L3ParseResult
from autoskillit.fleet.state import (
    DispatchRecord,
    DispatchStateHandle,
    DispatchStatus,
    normalize_dispatch_token_usage,
)
from autoskillit.fleet.state_types import (
    _ABANDON_REASONS,
    DispatchCompleted,
    DispatchEffectProvenance,
    DispatchResult,
)

ENVELOPE_STDERR_MAX = 2000


class _DispatchCommonFields(TypedDict):
    dispatch_id: str
    dispatched_session_id: str
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


def classify_dispatch_outcome(
    parsed: L3ParseResult | None,
    skill_result: SkillResult,
    *,
    sidecar_exists: bool = False,
    checkpoint: SessionCheckpoint | None = None,
    subtype: str = "",
) -> tuple[DispatchStatus, str]:
    """Map L2 food truck subprocess signals to a (DispatchStatus, reason) pair.

    Pure function — no filesystem access, no side effects.
    Rules applied in order:
      0. timeout + session_id + lifespan_started + (checkpoint or sidecar)
         + not abandon → RESUMABLE
      0b. timeout (any other case) → FAILURE
      1. completed_clean + success flag → SUCCESS
      2. completed_clean + no success → FAILURE
      3. completed_dirty → FAILURE (fleet_l3_parse_failed)
      4. no_sentinel + session_id + lifespan_started + (checkpoint or sidecar)
         + not abandon → RESUMABLE
      5. no_sentinel (any other case) → FAILURE (fleet_l3_no_result_block)
    """
    if subtype == "timeout":
        has_progress = checkpoint is not None or sidecar_exists
        if skill_result.session_id and skill_result.lifespan_started and has_progress:
            if _is_abandon_reason(skill_result):
                return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_TIMEOUT
            return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_L3_TIMEOUT
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_TIMEOUT

    if parsed is None:
        has_progress = checkpoint is not None or sidecar_exists
        if skill_result.session_id and skill_result.lifespan_started and has_progress:
            if _is_abandon_reason(skill_result):
                return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
            return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    if parsed.outcome == "completed_clean" and parsed.payload and parsed.payload.get("success"):
        reason = _sanitize_managed_capture_diagnostics(str(parsed.payload.get("reason", "")))
        return DispatchStatus.SUCCESS, reason
    if parsed.outcome == "completed_clean" and parsed.payload:
        reason = parsed.payload.get("reason", "")
        if reason == FleetErrorCode.FLEET_QUOTA_EXHAUSTED:
            return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_QUOTA_EXHAUSTED
    if parsed.outcome == "completed_clean":
        raw_reason = str(parsed.payload.get("reason", "")) if parsed.payload else ""
        reason = _sanitize_managed_capture_diagnostics(raw_reason)
        if raw_reason and not reason:
            reason = FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
        return DispatchStatus.FAILURE, reason
    if parsed.outcome == "completed_dirty":
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_PARSE_FAILED
    has_progress = checkpoint is not None or sidecar_exists
    if skill_result.session_id and skill_result.lifespan_started and has_progress:
        if _is_abandon_reason(skill_result):
            return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
        return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
    return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK


def build_dispatch_result(
    *,
    parsed_result: L3ParseResult | None,
    result_success: bool,
    final_status: DispatchStatus,
    reason: str,
    dispatch_id: str,
    skill_result: SkillResult,
    dispatch_checkpoint: SessionCheckpoint | None,
    started_at: float,
    ended_at: float,
    state_path: Path,
    effect_provenance: DispatchEffectProvenance,
) -> DispatchResult:
    """Build the bounded fleet envelope without exposing managed diagnostics."""
    common: _DispatchCommonFields = {
        "dispatch_id": dispatch_id,
        "dispatched_session_id": skill_result.session_id or "",
        "token_usage": normalize_dispatch_token_usage(skill_result.token_usage or {}),
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
                success=result_success,
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
                success=False,
                dispatch_status=final_status,
                reason=FleetErrorCode.FLEET_L3_PARSE_FAILED,
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
            success=False,
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
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id=record.dispatch_id,
            dispatched_session_id=record.dispatched_session_id,
            reason=record.reason,
            effect_provenance=effect_provenance,
            token_usage=dict(record.token_usage),
        ),
        per_dispatch_state_path=handle.state_path,
    )
