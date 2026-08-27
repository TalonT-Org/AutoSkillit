"""Structural-immunity suite for the ``state_types`` decomposition (#4856).

Each test should fail against the pre-decomposition monolith and pass once
the five focused modules plus the facade are in place. The decomposition
is structurally invisible to behavior — every public symbol retains its
qualname, value, and shape — so this suite verifies the structural
invariants rather than exercising behavior already covered elsewhere.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import FunctionType

import pytest

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
    TERMINAL_DISPATCH_STATUSES,
    TERMINAL_UNCLEANED_STATUSES,
    DispatchStatus,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _assert_facade_reexports(
    expected: tuple[tuple[str, object], ...],
    canonical_module: str,
) -> None:
    """Assert the facade re-exports each symbol and pins each class/function's home module.

    Constants and union aliases carry no usable ``__module__`` (a ``str`` has
    none, and ``DispatchOutcome`` reports ``types``), so the canonical-home
    check applies only to classes and functions.
    """
    for name, symbol in expected:
        assert getattr(state_types_facade_module, name) is symbol
        if isinstance(symbol, type | FunctionType):
            assert symbol.__module__ == canonical_module, (
                f"{name} should live in {canonical_module}, found {symbol.__module__}"
            )


def test_state_effects_module_importable() -> None:
    """Effect enums + records + tracker import from state_effects and re-export via the facade."""
    _assert_facade_reexports(
        (
            ("DispatchEffectName", DispatchEffectName),
            ("DispatchEffectPhase", DispatchEffectPhase),
            ("DispatchAggregatePhase", DispatchAggregatePhase),
            ("DispatchRetryDisposition", DispatchRetryDisposition),
            ("DispatchEffectRecord", DispatchEffectRecord),
            ("DispatchEffectProvenance", DispatchEffectProvenance),
            ("DispatchProvenanceTracker", DispatchProvenanceTracker),
        ),
        "autoskillit.fleet.state_effects",
    )


def test_state_records_module_importable() -> None:
    """DispatchRecord, CampaignState, ResumeDecision import from state_records."""
    _assert_facade_reexports(
        (
            ("FLEET_HALTED_SENTINEL", FLEET_HALTED_SENTINEL),
            ("FLEET_STATE_SCHEMA_VERSION", FLEET_STATE_SCHEMA_VERSION),
            ("DispatchRecord", DispatchRecord),
            ("CampaignState", CampaignState),
            ("ResumeDecision", ResumeDecision),
        ),
        "autoskillit.fleet.state_records",
    )


def test_state_transitions_module_importable() -> None:
    """DispatchStatus and the terminal-status sets re-export through the facade."""
    _assert_facade_reexports(
        (
            ("DispatchStatus", DispatchStatus),
            ("TERMINAL_DISPATCH_STATUSES", TERMINAL_DISPATCH_STATUSES),
            ("TERMINAL_UNCLEANED_STATUSES", TERMINAL_UNCLEANED_STATUSES),
        ),
        "autoskillit.fleet.state_transitions",
    )


def test_state_outcomes_module_importable() -> None:
    """Outcome/result types import from state_outcomes and re-export through the facade."""
    _assert_facade_reexports(
        (
            ("DispatchRejected", DispatchRejected),
            ("DispatchCompleted", DispatchCompleted),
            ("DispatchOutcome", DispatchOutcome),
            ("DispatchResult", DispatchResult),
            ("GateRecordResult", GateRecordResult),
        ),
        "autoskillit.fleet.state_outcomes",
    )


def test_state_error_codes_module_importable() -> None:
    """Error-code categorization symbols import from state_error_codes (underscore-prefixed)."""
    assert callable(get_error_category)
    assert isinstance(_ERROR_CODE_CATEGORIES, dict)
    assert isinstance(_INFRASTRUCTURE_FAILURE_REASONS, frozenset)
    _assert_facade_reexports(
        (("get_error_category", get_error_category),),
        "autoskillit.fleet.state_error_codes",
    )


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
    assert isinstance(state_module._resume_lock, type(threading.Lock()))
    assert not hasattr(state_types_facade_module, "_resume_lock")


def _names_a_state_records_module(dotted: str) -> bool:
    """True when a dotted module path resolves to state_records itself.

    Anchored on the final segment so a future sibling such as
    ``state_records_v2`` does not false-match, and leading dots from relative
    imports are stripped first.
    """
    return dotted.lstrip(".").rsplit(".", 1)[-1] == "state_records"


def test_state_transitions_does_not_import_state_records() -> None:
    """The documented one-way arrow state_records -> state_transitions is enforced, not just prose.

    state_transitions.py's docstring claims it imports nothing from
    state_records. Nothing else in the suite pins that direction:
    test_layer_enforcement.py covers cross-package arrows only, and the
    import-linter contracts do not describe intra-fleet edges.

    Both import forms are checked: ``from ... import`` (ast.ImportFrom) and
    plain ``import autoskillit.fleet.state_records`` (ast.Import). Checking
    only the former would let the plain form defeat the whole assertion.
    TYPE_CHECKING-guarded imports count as violations too — the docstring
    claims the module imports nothing from state_records, not merely nothing
    at runtime.
    """
    transitions_source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "fleet"
        / "state_transitions.py"
    )
    tree = ast.parse(transitions_source.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is not None and _names_a_state_records_module(node.module):
                offenders.append(node.module)
        elif isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if _names_a_state_records_module(alias.name)
            )
    assert not offenders, (
        f"state_transitions must not import from state_records; found {offenders}"
    )
