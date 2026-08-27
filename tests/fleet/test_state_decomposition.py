"""Structural-immunity suite for the fleet state-concern decomposition (#4856).

Each test should fail against the pre-decomposition monolith and pass once
the five focused modules are in place. The decomposition is structurally
invisible to behavior — every public symbol retains its qualname, value, and
shape — so this suite verifies the structural invariants rather than
exercising behavior already covered elsewhere.
"""

from __future__ import annotations

import ast
import importlib
import threading
from pathlib import Path
from types import FunctionType

import pytest

from autoskillit.fleet import state as state_module
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


def _assert_canonical_home(
    expected: tuple[tuple[str, object], ...],
    canonical_module: str,
) -> None:
    """Assert each symbol is reachable from its canonical module under its own name.

    Identity against the canonical module covers every symbol, including the
    constants and the ``DispatchOutcome`` union alias. Classes and functions
    additionally carry a meaningful ``__module__``, which is asserted directly;
    constants and union aliases do not (a ``str`` exposes none, and
    ``DispatchOutcome`` reports ``types``), so for those the identity check is
    the whole assertion.
    """
    home = importlib.import_module(canonical_module)
    for name, symbol in expected:
        assert getattr(home, name) is symbol
        if isinstance(symbol, type | FunctionType):
            assert symbol.__module__ == canonical_module, (
                f"{name} should live in {canonical_module}, found {symbol.__module__}"
            )


def test_state_effects_owns_effect_symbols() -> None:
    """Effect enums, records, and the tracker have state_effects as their canonical home."""
    _assert_canonical_home(
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


def test_state_records_owns_record_symbols() -> None:
    """DispatchRecord, CampaignState, ResumeDecision, and the constants live in state_records."""
    _assert_canonical_home(
        (
            ("FLEET_HALTED_SENTINEL", FLEET_HALTED_SENTINEL),
            ("FLEET_STATE_SCHEMA_VERSION", FLEET_STATE_SCHEMA_VERSION),
            ("DispatchRecord", DispatchRecord),
            ("CampaignState", CampaignState),
            ("ResumeDecision", ResumeDecision),
        ),
        "autoskillit.fleet.state_records",
    )


def test_state_transitions_owns_status_symbols() -> None:
    """DispatchStatus and the terminal-status sets live in state_transitions."""
    _assert_canonical_home(
        (
            ("DispatchStatus", DispatchStatus),
            ("TERMINAL_DISPATCH_STATUSES", TERMINAL_DISPATCH_STATUSES),
            ("TERMINAL_UNCLEANED_STATUSES", TERMINAL_UNCLEANED_STATUSES),
        ),
        "autoskillit.fleet.state_transitions",
    )


def test_state_outcomes_owns_outcome_symbols() -> None:
    """Outcome and result types live in state_outcomes."""
    _assert_canonical_home(
        (
            ("DispatchRejected", DispatchRejected),
            ("DispatchCompleted", DispatchCompleted),
            ("DispatchOutcome", DispatchOutcome),
            ("DispatchResult", DispatchResult),
            ("GateRecordResult", GateRecordResult),
        ),
        "autoskillit.fleet.state_outcomes",
    )


def test_state_error_codes_owns_categorization_symbols() -> None:
    """Error-code categorization symbols live in state_error_codes."""
    assert isinstance(_ERROR_CODE_CATEGORIES, dict)
    assert isinstance(_INFRASTRUCTURE_FAILURE_REASONS, frozenset)
    _assert_canonical_home(
        (("get_error_category", get_error_category),),
        "autoskillit.fleet.state_error_codes",
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
    """The _resume_lock belongs to the I/O layer in state.py."""
    assert isinstance(state_module._resume_lock, type(threading.Lock()))


def test_state_types_facade_is_gone() -> None:
    """The transitional re-export facade was removed; consumers use focused modules.

    Pins the deletion so the facade cannot reappear as a convenience shim
    without a deliberate decision — AGENTS.md 3.1 forbids backward-compat
    re-export modules.
    """
    fleet_dir = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "fleet"
    assert not (fleet_dir / "state_types.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.fleet.state_types")


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
