"""Audit outcome JSON rendering, materialization status mapping, and the
resumed-session audit completion path.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from autoskillit.core import (
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPrepareRequest,
    KillReason,
    compute_bytes_hash,
)
from autoskillit.server._recipe_execution import (
    required_audit_finalization_effect_names as _required_audit_finalization_effect_names,
)
from autoskillit.server.tools import tools_execution as _te_pkg

if TYPE_CHECKING:
    from pathlib import Path

    from autoskillit.core import (
        AuditAttemptId,
        AuditIdentityReservation,
        AuditVerdict,
        TrackerAuthorityTarget,
    )
    from autoskillit.pipeline import ToolContext


def _audit_response(
    *,
    status: AuditOutcomeStatus,
    attempt_id: AuditAttemptId,
    verdict: AuditVerdict | None,
    path: Path | None,
    error: str | None,
    kill_reason: KillReason = KillReason.NATURAL_EXIT,
) -> str:
    success = status in {
        AuditOutcomeStatus.PUBLISHED,
        AuditOutcomeStatus.EXACT_REPLAY,
    }
    return json.dumps(
        {
            "success": success,
            "exit_code": 0 if success else 1,
            "kill_reason": kill_reason.value,
            "result": (
                f"Server-authored audit outcome: {status.value}"
                if success
                else f"Audit admission failed: {error or status.value}"
            ),
            "audit_status": status.value,
            "audit_verdict": verdict.value if verdict is not None else None,
            "audit_cycle_path": str(path) if path is not None else None,
            "audit_attempt_id": attempt_id.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _materialization_outcome_status(
    result: AuditMaterializationResult,
) -> AuditOutcomeStatus:
    match result.status:
        case AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION:
            return AuditOutcomeStatus.PUBLISHED
        case AuditMaterializationStatus.SEMANTIC_REJECTED:
            return AuditOutcomeStatus.SEMANTIC_REJECTED
        case AuditMaterializationStatus.CONFLICT:
            return AuditOutcomeStatus.CONFLICT
        case AuditMaterializationStatus.STORAGE_FAILURE:
            return AuditOutcomeStatus.STORAGE_FAILURE
        case AuditMaterializationStatus.QUARANTINED:
            return AuditOutcomeStatus.QUARANTINED


def _reject_missing_semantic_result(
    tool_ctx: ToolContext,
    reservation: AuditIdentityReservation,
) -> AuditMaterializationResult:
    """Terminally reject a successful child that omitted its semantic artifact."""
    prepared = tool_ctx.audit_admission_ledger.prepare(
        AuditPrepareRequest(
            attempt_id=reservation.current_attempt_id,
            installation_version=reservation.slot_key.installation_version,
            semantic_digest=compute_bytes_hash(b""),
            accepted=False,
        )
    )
    if prepared.conflict_detail is not None:
        return AuditMaterializationResult(
            status=AuditMaterializationStatus.CONFLICT,
            attempt_id=reservation.current_attempt_id,
            verdict=None,
            path=None,
            error=prepared.conflict_detail,
        )
    return AuditMaterializationResult(
        status=AuditMaterializationStatus.SEMANTIC_REJECTED,
        attempt_id=reservation.current_attempt_id,
        verdict=None,
        path=None,
        error="successful audit child omitted audit_semantic_result_path",
    )


def _complete_resumed_audit(
    tool_ctx: ToolContext,
    *,
    result: AuditMaterializationResult,
    skill_command: str,
    tracker_target: TrackerAuthorityTarget | None = None,
) -> str:
    status = _materialization_outcome_status(result)
    if status is AuditOutcomeStatus.PUBLISHED:
        assert result.verdict is not None
        assert result.path is not None
        response = _audit_response(
            status=status,
            attempt_id=result.attempt_id,
            verdict=result.verdict,
            path=result.path,
            error=None,
        )
        replay_payload = json.loads(response)
        replay_payload["audit_status"] = AuditOutcomeStatus.EXACT_REPLAY.value
        replay_response = json.dumps(
            replay_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _te_pkg._complete_audit_finalization_effects(
            tool_ctx,
            attempt_id=result.attempt_id,
            skill_command=skill_command,
        )
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=result.attempt_id,
            verdict=result.verdict,
            path=result.path,
            error=None,
            replay_response_json=replay_response,
            tracker_target_order_id=(
                tracker_target.target_order_id if tracker_target is not None else None
            ),
            tracker_expected=(tracker_target.expected if tracker_target is not None else False),
        )
        tool_ctx.audit_admission_ledger.finalize_response(
            result.attempt_id,
            outcome,
            required_effect_names=_required_audit_finalization_effect_names(),
        )
        return response
    return _audit_response(
        status=status,
        attempt_id=result.attempt_id,
        verdict=result.verdict,
        path=result.path,
        error=result.error,
    )
