"""Ambient environment variable leak doctor checks."""

from __future__ import annotations

import os

from autoskillit.core import SESSION_TYPE_ENV_VAR, Severity, get_logger

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _check_ambient_session_type_skill() -> DoctorResult:
    """Detect ambient SESSION_TYPE=skill — common env leakage from fleet subprocesses."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() in ("skill", "leaf"):
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_skill",
            "Ambient SESSION_TYPE=skill detected. "
            "Did you intend to set SESSION_TYPE=skill? Fleet sessions should set "
            "SESSION_TYPE=skill only in launched subprocesses.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_skill",
        f"SESSION_TYPE={raw!r} (not skill)",
    )


def _check_ambient_session_type_orchestrator() -> DoctorResult:
    """Detect ambient SESSION_TYPE=orchestrator outside a launched session."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() == "orchestrator":
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_orchestrator",
            "Ambient SESSION_TYPE=orchestrator outside of a launched session "
            "— should only be set by autoskillit CLIs.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_orchestrator",
        "No ambient orchestrator session type",
    )


def _check_ambient_session_type_fleet() -> DoctorResult:
    """Detect ambient SESSION_TYPE=fleet outside a fleet CLI session."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() == "fleet":
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_fleet",
            "Ambient SESSION_TYPE=fleet outside of a fleet CLI session "
            "— highest-privilege env, suspicious.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_fleet",
        "No ambient fleet session type",
    )


def _check_ambient_campaign_id() -> DoctorResult:
    """Detect ambient CAMPAIGN_ID — should only be set by dispatch_food_truck."""
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    if campaign_id:
        return DoctorResult(
            Severity.WARNING,
            "ambient_campaign_id",
            f"Ambient CAMPAIGN_ID={campaign_id} — should only be set by dispatch_food_truck.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_campaign_id",
        "No ambient CAMPAIGN_ID",
    )
