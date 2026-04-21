"""Franchise sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.franchise``, not from sub-modules.
"""

from ._api import execute_dispatch
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
from .summary import (
    CampaignSummary,
    CampaignSummaryStatus,
    DispatchTokenUsage,
    PerDispatchEntry,
    SummaryErrorRecord,
    parse_campaign_summary,
    serialize_campaign_summary,
    validate_campaign_summary,
)

__all__ = [
    "execute_dispatch",
    "CampaignSummary",
    "CampaignSummaryStatus",
    "DispatchTokenUsage",
    "PerDispatchEntry",
    "SummaryErrorRecord",
    "parse_campaign_summary",
    "serialize_campaign_summary",
    "validate_campaign_summary",
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
