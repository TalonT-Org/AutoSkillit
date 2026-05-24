"""Dispatch outcome classification."""

from __future__ import annotations

from typing import Any

from autoskillit.core import (
    FleetErrorCode,
    InfraExitCategory,
    RetryReason,
    SessionCheckpoint,
    SkillResult,
)
from autoskillit.fleet.result_parser import L3ParseResult
from autoskillit.fleet.state import DispatchStatus
from autoskillit.fleet.state_types import _ABANDON_REASONS


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
    return {
        "completed_items": list(cp.completed_items),
        "step_name": cp.step_name,
        "progress_pct": cp.progress_pct,
        "ts": cp.ts,
    }


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
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK

    if parsed.outcome == "completed_clean" and parsed.payload and parsed.payload.get("success"):
        return DispatchStatus.SUCCESS, ""
    if parsed.outcome == "completed_clean":
        reason = parsed.payload.get("reason", "") if parsed.payload else ""
        return DispatchStatus.FAILURE, reason
    if parsed.outcome == "completed_dirty":
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_PARSE_FAILED
    has_progress = checkpoint is not None or sidecar_exists
    if skill_result.session_id and skill_result.lifespan_started and has_progress:
        if _is_abandon_reason(skill_result):
            return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
        return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
    return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
