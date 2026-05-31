"""Fleet sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.fleet``, not from sub-modules.
"""

from ._api import _write_pid as _write_pid
from ._api import execute_dispatch
from ._capture import CaptureCompletenessError
from ._checkpoint_bridge import checkpoint_from_sidecar
from ._dispatch_reaper import reap_stale_dispatches, reap_stale_dispatches_async
from ._expressions import evaluate_skip_when
from ._label_cleanup import (
    cleanup_orphaned_labels,
    discover_campaign_state_files,
    sweep_stale_dispatch_labels,
)
from ._liveness import is_dispatch_session_alive
from ._outcome import classify_dispatch_outcome
from ._prompts import _build_admiral_dispatch_block as _build_admiral_dispatch_block
from ._prompts import _build_food_truck_prompt as _build_food_truck_prompt
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
    TERMINAL_UNCLEANED_STATUSES,
    CampaignState,
    CampaignStateMutator,
    DispatchCompleted,
    DispatchRecord,
    DispatchRejected,
    DispatchResult,
    DispatchStateHandle,
    DispatchStatus,
    GateRecordResult,
    ResumeDecision,
    append_dispatch_record,
    build_protected_campaign_ids,
    has_failed_dispatch,
    mark_dispatch_interrupted,
    mark_dispatch_resumable,
    mark_dispatch_running,
    normalize_dispatch_token_usage,
    read_all_campaign_captures,
    read_state,
    record_gate_outcome,
    reset_blocking_dispatch,
    resume_campaign_from_state,
    update_orchestrator_session_id,
    upsert_dispatch_record_by_name,
    write_captured_values,
    write_initial_state,
)
from .state_recovery import (
    classify_stale_dispatch,
    derive_orchestrator_resume_spec,
    find_dispatch_for_issue,
    has_blocking_dispatch,
    has_completed_dispatch,
)
from .state_types import (
    _INFRASTRUCTURE_FAILURE_REASONS,  # noqa: F401
    FLEET_STATE_SCHEMA_VERSION,
    DispatchOutcome,
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
    "cleanup_orphaned_labels",
    "discover_campaign_state_files",
    "evaluate_skip_when",
    "sweep_stale_dispatch_labels",
    "CaptureCompletenessError",
    "classify_dispatch_outcome",
    "execute_dispatch",
    "_build_food_truck_prompt",
    "_build_admiral_dispatch_block",
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
    "TERMINAL_UNCLEANED_STATUSES",
    "FLEET_HALTED_SENTINEL",
    "CampaignState",
    "CampaignStateMutator",
    "DispatchCompleted",
    "DispatchRecord",
    "DispatchRejected",
    "DispatchResult",
    "DispatchStateHandle",
    "DispatchStatus",
    "DispatchOutcome",
    "ResumeDecision",
    "GateRecordResult",
    "append_dispatch_record",
    "build_protected_campaign_ids",
    "has_blocking_dispatch",
    "has_completed_dispatch",
    "has_failed_dispatch",
    "record_gate_outcome",
    "mark_dispatch_interrupted",
    "mark_dispatch_resumable",
    "mark_dispatch_running",
    "read_all_campaign_captures",
    "read_state",
    "reset_blocking_dispatch",
    "resume_campaign_from_state",
    "update_orchestrator_session_id",
    "upsert_dispatch_record_by_name",
    "write_captured_values",
    "write_initial_state",
    "normalize_dispatch_token_usage",
    "classify_stale_dispatch",
    "FLEET_STATE_SCHEMA_VERSION",
    "derive_orchestrator_resume_spec",
    "find_dispatch_for_issue",
    "checkpoint_from_sidecar",
    "is_dispatch_session_alive",
    "reap_stale_dispatches",
    "reap_stale_dispatches_async",
    "FleetSemaphore",
    "IssueSidecarEntry",
    "append_sidecar_entry",
    "compute_remaining_issues",
    "read_sidecar",
    "read_sidecar_from_path",
    "sidecar_path",
]
