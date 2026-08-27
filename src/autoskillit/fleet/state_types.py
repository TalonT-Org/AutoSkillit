"""Backwards-compatible facade re-exporting state-concern modules.

Decomposed from a single 899-line monolith (#4856); see state_effects,
state_records, state_transitions, state_outcomes, and state_error_codes
for the canonical homes of each symbol. New consumers should import from
the focused module; this facade exists to keep legacy
``from autoskillit.fleet.state_types import X`` paths working for the
public surface only. Underscore-prefixed helpers are NOT re-exported —
import them directly from the focused module that owns them.
"""

from autoskillit.fleet.state_effects import (
    DispatchAggregatePhase,
    DispatchEffectName,
    DispatchEffectPhase,
    DispatchEffectProvenance,
    DispatchEffectRecord,
    DispatchProvenanceTracker,
    DispatchRetryDisposition,
)
from autoskillit.fleet.state_error_codes import get_error_category
from autoskillit.fleet.state_outcomes import (
    DispatchCompleted,
    DispatchOutcome,
    DispatchRejected,
    DispatchResult,
    GateRecordResult,
)
from autoskillit.fleet.state_records import (
    FLEET_HALTED_SENTINEL,
    FLEET_STATE_SCHEMA_VERSION,
    CampaignState,
    DispatchRecord,
    ResumeDecision,
)
from autoskillit.fleet.state_transitions import (
    TERMINAL_DISPATCH_STATUSES,
    TERMINAL_UNCLEANED_STATUSES,
    DispatchStatus,
)

__all__ = [
    # state_effects
    "DispatchAggregatePhase",
    "DispatchEffectName",
    "DispatchEffectPhase",
    "DispatchEffectProvenance",
    "DispatchEffectRecord",
    "DispatchProvenanceTracker",
    "DispatchRetryDisposition",
    # state_error_codes
    "get_error_category",
    # state_outcomes
    "DispatchCompleted",
    "DispatchOutcome",
    "DispatchRejected",
    "DispatchResult",
    "GateRecordResult",
    # state_records
    "CampaignState",
    "DispatchRecord",
    "FLEET_HALTED_SENTINEL",
    "FLEET_STATE_SCHEMA_VERSION",
    "ResumeDecision",
    # state_transitions
    "DispatchStatus",
    "TERMINAL_DISPATCH_STATUSES",
    "TERMINAL_UNCLEANED_STATUSES",
]
