"""Fleet sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.fleet``, not from sub-modules.
"""

from ._api import _write_pid as _write_pid
from ._api import classify_dispatch_outcome, execute_dispatch
from ._checkpoint_bridge import checkpoint_from_sidecar
from ._liveness import is_dispatch_session_alive
from ._prompts import _build_food_truck_prompt as _build_food_truck_prompt
from ._prompts import _build_l3_sous_chef_block as _build_l3_sous_chef_block
from ._semaphore import FleetSemaphore
from .result_parser import L3ParseResult, parse_l3_result_block
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
    TERMINAL_DISPATCH_STATUSES,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    GateRecordResult,
    ResumeDecision,
    append_dispatch_record,
    build_protected_campaign_ids,
    crash_recover_dispatch,
    has_failed_dispatch,
    mark_dispatch_interrupted,
    mark_dispatch_resumable,
    mark_dispatch_running,
    read_all_campaign_captures,
    read_state,
    record_gate_outcome,
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
    "_write_pid",
    "classify_dispatch_outcome",
    "execute_dispatch",
    "_build_food_truck_prompt",
    "_build_l3_sous_chef_block",
    "L3ParseResult",
    "parse_l3_result_block",
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
    "TERMINAL_DISPATCH_STATUSES",
    "FLEET_HALTED_SENTINEL",
    "CampaignState",
    "DispatchRecord",
    "DispatchStatus",
    "ResumeDecision",
    "GateRecordResult",
    "append_dispatch_record",
    "crash_recover_dispatch",
    "build_protected_campaign_ids",
    "has_failed_dispatch",
    "record_gate_outcome",
    "mark_dispatch_interrupted",
    "mark_dispatch_resumable",
    "mark_dispatch_running",
    "read_all_campaign_captures",
    "read_state",
    "resume_campaign_from_state",
    "write_captured_values",
    "write_initial_state",
    "checkpoint_from_sidecar",
    "is_dispatch_session_alive",
    "FleetSemaphore",
    "IssueSidecarEntry",
    "append_sidecar_entry",
    "compute_remaining_issues",
    "read_sidecar",
    "read_sidecar_from_path",
    "sidecar_path",
]
