"""Fleet sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.fleet``, not from sub-modules.
"""

from ._api import execute_dispatch
from ._liveness import is_dispatch_session_alive
from ._prompts import _build_food_truck_prompt as _build_food_truck_prompt
from ._prompts import _build_l2_sous_chef_block as _build_l2_sous_chef_block
from ._semaphore import FleetSemaphore
from .result_parser import L2ParseResult, parse_l2_result_block
from .sidecar import (
    IssueSidecarEntry,
    append_sidecar_entry,
    compute_remaining_issues,
    read_sidecar,
    read_sidecar_from_path,
    sidecar_path,
)
from .state import (
    FLEET_HALTED_SENTINEL,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
    append_dispatch_record,
    build_protected_campaign_ids,
    crash_recover_dispatch,
    mark_dispatch_interrupted,
    mark_dispatch_resumable,
    mark_dispatch_running,
    read_all_campaign_captures,
    read_state,
    resume_campaign_from_state,
    write_captured_values,
    write_initial_state,
)
from .summary import (
    CampaignParseResult,
    CampaignSummary,
    CampaignSummaryStatus,
    DispatchTokenUsage,
    ParseFailure,
    ParseFailureKind,
    PerDispatchEntry,
    SummaryErrorRecord,
    parse_campaign_summary,
    serialize_campaign_summary,
    validate_campaign_summary,
)

__all__ = [
    "execute_dispatch",
    "_build_food_truck_prompt",
    "_build_l2_sous_chef_block",
    "L2ParseResult",
    "parse_l2_result_block",
    "CampaignParseResult",
    "CampaignSummary",
    "CampaignSummaryStatus",
    "DispatchTokenUsage",
    "ParseFailure",
    "ParseFailureKind",
    "PerDispatchEntry",
    "SummaryErrorRecord",
    "parse_campaign_summary",
    "serialize_campaign_summary",
    "validate_campaign_summary",
    "FLEET_HALTED_SENTINEL",
    "CampaignState",
    "DispatchRecord",
    "DispatchStatus",
    "ResumeDecision",
    "append_dispatch_record",
    "crash_recover_dispatch",
    "build_protected_campaign_ids",
    "mark_dispatch_interrupted",
    "mark_dispatch_resumable",
    "mark_dispatch_running",
    "read_all_campaign_captures",
    "read_state",
    "resume_campaign_from_state",
    "write_captured_values",
    "write_initial_state",
    "is_dispatch_session_alive",
    "FleetSemaphore",
    "IssueSidecarEntry",
    "append_sidecar_entry",
    "compute_remaining_issues",
    "read_sidecar",
    "read_sidecar_from_path",
    "sidecar_path",
]
