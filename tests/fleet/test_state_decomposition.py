"""Structural-immunity suite for the ``state_types`` decomposition (#4856).

Each test should fail against the pre-decomposition monolith and pass once
the five focused modules plus the facade are in place. The decomposition
is structurally invisible to behavior — every public symbol retains its
qualname, value, and shape — so this suite verifies the structural
invariants rather than exercising behavior already covered elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.fleet import state as state_module
from autoskillit.fleet import state_types as state_types_facade_module
from autoskillit.fleet.state_effects import (
    DispatchAggregatePhase,
    DispatchEffectName,
    DispatchEffectPhase,
    DispatchEffectProvenance,
    DispatchEffectRecord,
    DispatchProvenanceTracker,
    DispatchRetryDisposition,
)
from autoskillit.fleet.state_error_codes import (
    _ERROR_CODE_CATEGORIES,
    _INFRASTRUCTURE_FAILURE_REASONS,
    get_error_category,
)
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
    _ALLOWED_TRANSITIONS,
    DispatchStatus,
)


def test_state_effects_module_importable() -> None:
    """Effect enums + records + tracker import from state_effects and re-export via the facade."""
    facade = __import__("autoskillit.fleet.state_types", fromlist=["__all__"])
    for symbol in (
        DispatchEffectName,
        DispatchEffectPhase,
        DispatchAggregatePhase,
        DispatchRetryDisposition,
        DispatchEffectRecord,
        DispatchEffectProvenance,
        DispatchProvenanceTracker,
    ):
        assert getattr(facade, symbol.__name__) is symbol


def test_state_records_module_importable() -> None:
    """DispatchRecord, CampaignState, ResumeDecision import from state_records."""
    facade = __import__("autoskillit.fleet.state_types", fromlist=["__all__"])
    public_records_symbols = (
        FLEET_HALTED_SENTINEL,
        FLEET_STATE_SCHEMA_VERSION,
        DispatchRecord,
        CampaignState,
        ResumeDecision,
    )
    for symbol in public_records_symbols:
        assert getattr(facade, symbol.__name__) is symbol


def test_state_transitions_module_importable() -> None:
    """DispatchStatus and the transition-table symbols import from state_transitions."""
    facade = __import__("autoskillit.fleet.state_types", fromlist=["__all__"])
    assert getattr(facade, DispatchStatus.__name__) is DispatchStatus


def test_state_outcomes_module_importable() -> None:
    """Outcome/result types import from state_outcomes and re-export through the facade."""
    facade = __import__("autoskillit.fleet.state_types", fromlist=["__all__"])
    for symbol in (
        DispatchRejected,
        DispatchCompleted,
        DispatchOutcome,
        DispatchResult,
        GateRecordResult,
    ):
        assert getattr(facade, symbol.__name__) is symbol


def test_state_error_codes_module_importable() -> None:
    """Error-code categorization symbols import from state_error_codes (underscore-prefixed)."""
    assert callable(get_error_category)
    assert isinstance(_ERROR_CODE_CATEGORIES, dict)
    assert isinstance(_INFRASTRUCTURE_FAILURE_REASONS, frozenset)


def test_state_types_facade_does_not_reexport_underscore_helpers() -> None:
    """Facade exposes only public symbols — underscore helpers route through focused modules."""
    facade_all = set(getattr(state_types_facade_module, "__all__", ()))
    forbidden = {
        "_ALLOWED_TRANSITIONS",
        "_VISIBLE_IN_BLOCK_STATUSES",
        "_RETRY_IDENTITY_FIELDS",
        "_clear_dispatch_for_retry",
        "_validate_transition",
        "_normalize_effect_provenance",
        "_ERROR_CODE_CATEGORIES",
        "_INFRASTRUCTURE_FAILURE_REASONS",
        "_COMPLETED_STATUSES",
        "_ABANDON_REASONS",
    }
    leaked = forbidden & facade_all
    assert not leaked, (
        f"state_types facade must not re-export underscore helpers; leaked: {sorted(leaked)}"
    )


def test_dispatch_status_exhaustive_transitions_unchanged() -> None:
    """Every DispatchStatus member is a key in _ALLOWED_TRANSITIONS (assertion loop passes)."""
    for status in DispatchStatus:
        assert status in _ALLOWED_TRANSITIONS, (
            f"DispatchStatus.{status.name} missing from _ALLOWED_TRANSITIONS"
        )


def test_dispatch_status_retry_eligibility_reaches_pending() -> None:
    """Statuses eligible for retry via reset_blocking_dispatch must reach PENDING."""
    retry_eligible = {
        DispatchStatus.FAILURE,
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
    }
    for status in retry_eligible:
        assert DispatchStatus.PENDING in _ALLOWED_TRANSITIONS[status], (
            f"retry-eligible status {status.name} cannot transition to PENDING"
        )


def test_resume_lock_lives_in_state_module() -> None:
    """The _resume_lock moved to state.py — the facade no longer owns it."""
    assert hasattr(state_module, "_resume_lock")
    assert not hasattr(state_types_facade_module, "_resume_lock")


def test_fleet_module_count_within_new_exemption() -> None:
    """The fleet/ module count stays within the new EXEMPTIONS cap."""
    fleet_dir = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "fleet"
    count = sum(1 for _ in fleet_dir.glob("*.py"))
    assert count <= 29, (
        f"fleet/ has {count} top-level .py files; EXEMPTIONS['fleet'] bumped to 29 for #4856"
    )
