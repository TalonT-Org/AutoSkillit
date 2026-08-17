"""Provenance tracking helpers for fleet dispatch MCP tools."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from autoskillit.core import FleetErrorCode, fleet_error
from autoskillit.fleet import DispatchEffectName, DispatchProvenanceTracker

_BOUND_DISPATCH_PROVENANCE: ContextVar[DispatchProvenanceTracker | None] = ContextVar(
    "bound_dispatch_provenance",
    default=None,
)
_ACTIVE_DISPATCH_PROVENANCE: ContextVar[DispatchProvenanceTracker] = ContextVar(
    "active_dispatch_provenance"
)


def _attach_dispatch_provenance(
    raw: str,
    provenance: DispatchProvenanceTracker,
) -> str:
    """Attach the current immutable provenance snapshot to any JSON envelope."""
    try:
        envelope = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if not isinstance(envelope, dict):
        return raw
    envelope["effect_provenance"] = provenance.snapshot().to_dict()
    return json.dumps(envelope)


def _bound_dispatch_provenance() -> DispatchProvenanceTracker:
    provenance = _BOUND_DISPATCH_PROVENANCE.get()
    if provenance is None:
        raise RuntimeError("dispatch provenance binder was not initialized")
    return provenance


def _dispatch_cancellation_response(
    provenance: DispatchProvenanceTracker,
    _exc: asyncio.CancelledError,
) -> str:
    provenance.request_cancel()
    return _attach_dispatch_provenance(
        fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            "CancelledError: transport teardown",
        ),
        provenance,
    )


def _bind_dispatch_provenance(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Create one argument-aware provenance journal at the outer MCP boundary."""
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind_partial(*args, **kwargs)
        tracker = DispatchProvenanceTracker()
        requested_resume = str(bound.arguments.get("resume_session_id") or "")
        prior_dispatch = str(bound.arguments.get("prior_dispatch_id") or "")
        if requested_resume:
            tracker.start(
                DispatchEffectName.REQUESTED_RESUME_BINDING,
                retry_relevant=False,
                identities={
                    "resume_session_id": requested_resume,
                    "prior_dispatch_id": prior_dispatch,
                },
            )
            tracker.confirm(
                DispatchEffectName.REQUESTED_RESUME_BINDING,
                receipt="outer MCP request arguments bound",
                retry_relevant=False,
                identities={
                    "resume_session_id": requested_resume,
                    "prior_dispatch_id": prior_dispatch,
                },
            )
        token = _BOUND_DISPATCH_PROVENANCE.set(tracker)
        try:
            raw = await fn(*args, **kwargs)
            return _attach_dispatch_provenance(raw, tracker)
        finally:
            _BOUND_DISPATCH_PROVENANCE.reset(token)

    return wrapper


def _read_health_report(diagnostics_log_dir: Path, dispatch_id: str) -> dict[str, Any] | None:
    """Read the per-dispatch health report JSON written by analyze-pipeline-health."""
    report_path = diagnostics_log_dir / "health-reports" / f"{dispatch_id}_health_report.json"
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
