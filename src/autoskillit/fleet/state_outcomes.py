"""Fleet dispatch outcome / result authority.

Owns ``GateRecordResult``, ``DispatchRejected``, ``DispatchCompleted``,
``DispatchOutcome``, and ``DispatchResult``. Decomposed from ``state_types``
(#4856).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.core import FleetErrorCode
from autoskillit.fleet.state_effects import DispatchEffectProvenance
from autoskillit.fleet.state_transitions import DispatchStatus


@dataclass(frozen=True, slots=True)
class GateRecordResult:
    """Result of a gate dispatch recording attempt."""

    success: bool
    dispatch_name: str
    status: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class DispatchRejected:
    """Pre-validation or infrastructure rejection — no subprocess was launched."""

    error_code: FleetErrorCode
    message: str
    effect_provenance: DispatchEffectProvenance = field(kw_only=True)
    details: dict[str, Any] | None = None
    dispatch_id: str = ""

    def to_envelope(self) -> str:
        d: dict[str, Any] = {
            "kind": "rejected",
            "success": False,
            "error": self.error_code,
            "user_visible_message": self.message,
            "details": self.details,
            "effect_provenance": self.effect_provenance.to_dict(),
        }
        if self.dispatch_id:
            d["dispatch_id"] = self.dispatch_id
        return json.dumps(d)


@dataclass(frozen=True, slots=True)
class DispatchCompleted:
    """Dispatch that reached subprocess phase — may have succeeded or failed."""

    success: bool
    dispatch_status: DispatchStatus
    dispatch_id: str
    dispatched_session_id: str
    reason: str
    diagnostic_message: str = ""
    effect_provenance: DispatchEffectProvenance = field(kw_only=True)
    token_usage: dict[str, Any] = field(default_factory=dict)
    l3_payload: dict[str, Any] | None = None
    l3_parse_source: str = ""
    lifespan_started: bool = False
    l3_raw_body: str | None = None
    l3_parse_error: str | None = None
    resume_checkpoint: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""
    elapsed_seconds: float = 0.0
    health_report: dict[str, Any] | None = None

    def to_envelope(self) -> str:
        reason_text = str(self.reason)
        d: dict[str, Any] = {
            "kind": "completed",
            "success": self.success,
            "dispatch_status": self.dispatch_status.value,
            "dispatch_id": self.dispatch_id,
            "dispatched_session_id": self.dispatched_session_id,
            "reason": self.reason,
            "effect_provenance": self.effect_provenance.to_dict(),
            "token_usage": self.token_usage,
            "l3_payload": self.l3_payload,
            "l3_parse_source": self.l3_parse_source,
            "lifespan_started": self.lifespan_started,
            "stderr": self.stderr,
            "elapsed_seconds": self.elapsed_seconds,
        }
        if not self.success:
            d["error"] = reason_text or FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH.value
            d["user_visible_message"] = self.diagnostic_message or reason_text
        if self.l3_raw_body is not None:
            d["l3_raw_body"] = self.l3_raw_body
        if self.l3_parse_error is not None:
            d["l3_parse_error"] = self.l3_parse_error
        if self.resume_checkpoint:
            d["resume_checkpoint"] = self.resume_checkpoint
        if self.health_report is not None:
            d["health_report"] = self.health_report
        return json.dumps(d)


DispatchOutcome = DispatchCompleted | DispatchRejected


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Result of execute_dispatch: the outcome plus the per-dispatch state path.

    The per_dispatch_state_path is None when the dispatch never reached
    _run_dispatch (e.g., quota check failure, lock timeout).
    """

    outcome: DispatchOutcome
    per_dispatch_state_path: Path | None = None


__all__ = [
    "GateRecordResult",
    "DispatchCompleted",
    "DispatchOutcome",
    "DispatchRejected",
    "DispatchResult",
]
