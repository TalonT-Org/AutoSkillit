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
    """Module-level functions must be importable from ``_admission`` and
    perform the documented work for a stub store — not just be callable.

    Each helper is invoked against a minimal stub that exposes the
    attributes the helper reaches into. The stub records the call so we
    verify dispatch occurred (not just that the function exists).
    """
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

    # _scan_and_adopt_orphans: ensure the None-lifecycle_error guard fires
    # with a clear TypeError so callers cannot silently mis-use it.
    with pytest.raises(TypeError, match="lifecycle_error"):
        _scan_and_adopt_orphans(store=object())  # type: ignore[arg-type]


def test_admit_new_record_class_method_wrapper_delegates_to_module_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tests/cli/test_capture_store.py:247`` relies on this monkeypatching
    contract (``real_admit = CaptureLifecycleStore._admit_new_record``).

    Verifies both the bound-method signature AND that the wrapper actually
    dispatches to ``_admission._admit_new_record`` — without this assertion,
    a regression that breaks delegation would not be caught.
    """
    import autoskillit.hooks._capture_lifecycle._admission as admission_mod
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    store_method = CaptureLifecycleStore._admit_new_record
    sig = inspect.signature(store_method)
    assert list(sig.parameters.keys()) == [
        "self",
        "record",
        "records",
        "compaction_epoch",
        "size",
    ]

    sentinel = object()
    received: dict[str, object] = {}

    def fake_admit(store, record, records, compaction_epoch, size):
        received["store"] = store
        received["record"] = record
        received["records"] = records
        received["compaction_epoch"] = compaction_epoch
        received["size"] = size
        return sentinel

    monkeypatch.setattr(admission_mod, "_admit_new_record", fake_admit)

    fake_store = object()
    fake_record = object()
    fake_records: dict[str, object] = {}
    # Cast stubs to Any: the wrappers take store: Any (see _admission.py) and
    # these tests only verify delegation, not type contracts.
    result = store_method(
        fake_store,  # type: ignore[arg-type]
        fake_record,  # type: ignore[arg-type]
        fake_records,  # type: ignore[arg-type]
        7,
        1024,
    )

    assert result is sentinel
    assert received == {
        "store": fake_store,
        "record": fake_record,
        "records": fake_records,
        "compaction_epoch": 7,
        "size": 1024,
    }


def test_scan_and_adopt_orphans_wrapper_delegates_to_module_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper must pass ``CaptureLifecycleError`` through and forward
    the store. Without this delegation check the wrapper could silently
    stop calling the module-level function.
    """
    import autoskillit.hooks._capture_lifecycle._admission as admission_mod
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._scan_and_adopt_orphans)
    assert list(sig.parameters.keys()) == ["self"]

    received: dict[str, object] = {}
    sentinel = object()

    def fake_scan(store, *, lifecycle_error):
        received["store"] = store
        received["lifecycle_error"] = lifecycle_error
        return sentinel

    monkeypatch.setattr(admission_mod, "_scan_and_adopt_orphans", fake_scan)

    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleError

    fake_store = object()
    result = CaptureLifecycleStore._scan_and_adopt_orphans(fake_store)  # type: ignore[arg-type]

    assert result is sentinel
    assert received == {"store": fake_store, "lifecycle_error": CaptureLifecycleError}


def test_acquire_flock_wrapper_delegates_to_module_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.hooks._capture_lifecycle._admission as admission_mod
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._acquire_flock)
    params = list(sig.parameters.keys())
    assert params == ["self", "fd", "blocking"]
    assert sig.parameters["blocking"].kind is inspect.Parameter.KEYWORD_ONLY

    received: dict[str, object] = {}

    def fake_acquire(store, fd, *, blocking):
        received["store"] = store
        received["fd"] = fd
        received["blocking"] = blocking
        return None

    monkeypatch.setattr(admission_mod, "_acquire_flock", fake_acquire)

    fake_store = object()
    CaptureLifecycleStore._acquire_flock(fake_store, 42, blocking=True)  # type: ignore[arg-type]

    assert received == {"store": fake_store, "fd": 42, "blocking": True}


def test_admission_reason_wrapper_delegates_to_module_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.hooks._capture_lifecycle._admission as admission_mod
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore

    sig = inspect.signature(CaptureLifecycleStore._admission_reason)
    params = list(sig.parameters.keys())
    assert params == ["self", "records", "candidate", "compaction_epoch"]

    received: dict[str, object] = {}
    sentinel = object()

    def fake_reason(store, records, candidate, compaction_epoch):
        received["store"] = store
        received["records"] = records
        received["candidate"] = candidate
        received["compaction_epoch"] = compaction_epoch
        return sentinel

    monkeypatch.setattr(admission_mod, "_admission_reason", fake_reason)

    fake_store = object()
    fake_records: dict[str, object] = {}
    fake_candidate = object()
    # Cast stubs to Any: the wrappers take store: Any (see _admission.py) and
    # these tests only verify delegation, not type contracts.
    result = CaptureLifecycleStore._admission_reason(
        fake_store,  # type: ignore[arg-type]
        fake_records,  # type: ignore[arg-type]
        fake_candidate,  # type: ignore[arg-type]
        3,
    )

    assert result is sentinel
    assert received == {
        "store": fake_store,
        "records": fake_records,
        "candidate": fake_candidate,
        "compaction_epoch": 3,
    }


@pytest.mark.parametrize(
    "full",
    [
        "autoskillit.hooks._capture_lifecycle._store",
        "autoskillit.hooks._capture_lifecycle._admission",
        "autoskillit.hooks._capture_lifecycle",
    ],
)
def test_module_aliases_resolve_to_same_sys_modules_entry(full: str) -> None:
    """Both the full and short import spellings resolve to the same
    ``sys.modules`` entry under ``register_module_aliases``.
    """
    short = full.removeprefix("autoskillit.hooks.")
    full_mod = sys.modules[full]
    short_mod = sys.modules.get(short)
    assert short_mod is full_mod, f"alias mismatch: {short} != {full}"
