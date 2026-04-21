"""Franchise sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.franchise``, not from sub-modules.
"""

from .state import (
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
    append_dispatch_record,
    mark_dispatch_interrupted,
    mark_dispatch_running,
    read_state,
    resume_campaign_from_state,
    write_initial_state,
)

__all__ = [
    "CampaignState",
    "DispatchRecord",
    "DispatchStatus",
    "ResumeDecision",
    "append_dispatch_record",
    "mark_dispatch_interrupted",
    "mark_dispatch_running",
    "read_state",
    "resume_campaign_from_state",
    "write_initial_state",
]
