"""Franchise sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.franchise``, not from sub-modules.
"""

from ._api import execute_dispatch
from .result_parser import L2ParseResult, parse_l2_result_block
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
    "execute_dispatch",
    "L2ParseResult",
    "parse_l2_result_block",
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
