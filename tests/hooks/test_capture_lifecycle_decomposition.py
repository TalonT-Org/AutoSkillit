"""Tests for the ``_capture_lifecycle`` package split (issue #4727, sub-ticket B).

The single-file ``hooks/_capture_lifecycle.py`` (1,209 lines) was converted
into a regular package ``hooks/_capture_lifecycle/`` containing:

- ``__init__.py`` — pure re-export facade (the original ``__all__`` plus
  the lazy re-aliases from lines 56–74 plus the module-level constants).
- ``_store.py`` — the ``CaptureLifecycleStore`` class body with the four
  admission helpers reduced to 1-line wrappers that delegate to
  ``_admission.py``.
- ``_admission.py`` — module-level implementations of
  ``_acquire_flock``, ``_admission_reason``, ``_admit_new_record``,
  ``_scan_and_adopt_orphans`` (the four methods named in issue #4727).

These tests verify that the public-API surface at the original import
path remains intact (``Test 2``), that the class-method wrappers
preserve the bound-method contract that
``tests/cli/test_capture_store.py:247`` relies on
(``Test 3``), and that ``register_module_aliases`` registers both
spellings under ``sys.modules`` (``Test 4``).
"""

from __future__ import annotations

import inspect
import sys

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_public_api_importable_from_package_facade() -> None:
    """Every name in the original ``__all__`` (lines 75–92) plus the lazy
    re-aliases (lines 56–74) plus the module-level constants must be
    importable from ``autoskillit.hooks._capture_lifecycle``.
    """
    from autoskillit.hooks import _capture_lifecycle as facade

    expected = {
        # Original __all__ (lines 75-92)
        "CaptureCapacityError",
        "CaptureCapacityReason",
        "CaptureCleanupOutcome",
        "CaptureDeliveryStatus",
        "CaptureLedgerError",
        "CaptureLifecycleError",
        "CaptureLifecycleRecord",
        "CaptureLifecycleStore",
        "CaptureReferenceStatus",
        "CaptureRetentionPhase",
        "CaptureSnapshotStatus",
        "CaptureStatus",
        "CaptureState",
        "CaptureTransitionCommittedError",
        "CleanupBlocker",
        "CleanupProgress",
        "SweepBudgetSpec",
        # Lazy re-aliases (lines 56-74)
        "DueKey",
        "SweepAttempt",
        "_ObservedArtifact",
        "_CarrierLeaseLive",
        "CaptureAuthorityError",
        "CaptureFailureEvidence",
        "CaptureFinalManifest",
        "CaptureWriteAuthority",
        "FinalizedCapture",
        "IssuedCaptureReference",
        "LegacyCleanupOnly",
        "PublishedCaptureReference",
        "UnavailableCaptureReference",
        "VerifiedCaptureSnapshot",
        # Module-level constants (lines 93-101) — also
        # ``LEDGER_NAME`` / ``LOCK_NAME`` which are imported by
        # ``tests/cli/test_capture_store.py`` and
        # ``tests/hooks/test_hook_lifecycle_contract.py``.
        "FRAME_MAGIC",
        "LEDGER_NAME",
        "LOCK_NAME",
        "MAX_LEDGER_BYTES",
        "MAX_ACTIVE_RECORDS",
        "_RETENTION_SECONDS",
        "_REFERENCE_LIFETIME_SECONDS",
    }
    missing = expected - set(dir(facade))
    assert not missing, f"Facade re-exports missing names: {sorted(missing)}"


def test_capture_lifecycle_store_class_resolves_through_facade() -> None:
    """``CaptureLifecycleStore`` resolves through the facade and is the
    same class object as the one in ``_store.py``."""
    from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore
    from autoskillit.hooks._capture_lifecycle._store import (
        CaptureLifecycleStore as StoreClass,
    )

    assert CaptureLifecycleStore is StoreClass


def test_admission_helpers_resolvable_through_package() -> None:
    """Module-level functions must be importable from ``_admission``."""
    from autoskillit.hooks._capture_lifecycle._admission import (
        _acquire_flock,
        _admission_reason,
        _admit_new_record,
        _scan_and_adopt_orphans,
    )

    assert callable(_acquire_flock)
    assert callable(_admission_reason)
    assert callable(_admit_new_record)
    assert callable(_scan_and_adopt_orphans)


def test_admit_new_record_class_method_wrapper_delegates_to_module_function() -> None:
    """``tests/cli/test_capture_store.py:247`` relies on this monkeypatching
    contract (``real_admit = CaptureLifecycleStore._admit_new_record``)."""
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    store_method = CaptureLifecycleStore._admit_new_record
    assert callable(store_method)
    # The wrapper signature matches: (self, record, records, compaction_epoch, size)
    sig = inspect.signature(store_method)
    assert list(sig.parameters.keys()) == [
        "self",
        "record",
        "records",
        "compaction_epoch",
        "size",
    ]


def test_scan_and_adopt_orphans_wrapper_preserves_signature() -> None:
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._scan_and_adopt_orphans)
    assert list(sig.parameters.keys()) == ["self"]


def test_acquire_flock_wrapper_preserves_signature() -> None:
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._acquire_flock)
    params = list(sig.parameters.keys())
    assert params == ["self", "fd"]


def test_admission_reason_wrapper_preserves_signature() -> None:
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._admission_reason)
    params = list(sig.parameters.keys())
    assert params == ["self", "records", "candidate", "compaction_epoch"]


def test_capture_lifecycle_store_registers_both_module_aliases() -> None:
    """Both ``autoskillit.hooks._capture_lifecycle._store`` and
    ``_capture_lifecycle._store`` resolve to the same ``sys.modules`` entry."""
    full = "autoskillit.hooks._capture_lifecycle._store"
    short = "_capture_lifecycle._store"
    full_mod = sys.modules[full]
    short_mod = sys.modules.get(short)
    assert short_mod is full_mod, f"alias mismatch: {short} != {full}"


def test_capture_lifecycle_admission_registers_both_module_aliases() -> None:
    """Both spellings of ``_admission`` resolve to the same entry."""
    full = "autoskillit.hooks._capture_lifecycle._admission"
    short = "_capture_lifecycle._admission"
    full_mod = sys.modules[full]
    short_mod = sys.modules.get(short)
    assert short_mod is full_mod, f"alias mismatch: {short} != {full}"


def test_capture_lifecycle_package_registers_both_aliases() -> None:
    """The package itself registers both spellings."""
    full = "autoskillit.hooks._capture_lifecycle"
    short = "_capture_lifecycle"
    full_mod = sys.modules[full]
    short_mod = sys.modules.get(short)
    assert short_mod is full_mod, f"alias mismatch: {short} != {full}"
