"""Module-level admission helpers extracted from ``CaptureLifecycleStore``.

The four class methods ``_acquire_flock``, ``_admission_reason``,
``_admit_new_record``, and ``_scan_and_adopt_orphans`` were converted into
1-line wrappers in ``_store.py`` so they remain available as bound methods
(preserving the ``monkeypatch.setattr`` contract used by
``tests/cli/test_capture_store.py``). The function bodies live here so that
the lock-retry primitive, the capacity-admission check, and the
orphan-adoption call are physically separate from the store class. The
``#4440`` one-record-cannot-starve invariant is preserved by moving the
``_sweep_transitions`` budget increment alongside the admission check
into ``_admit_new_record`` below.
"""

from __future__ import annotations

import errno
import fcntl
import importlib
import random
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Bootstrap block — mirrors ``_capture/_authority.py`` so that bare-name
# ``_capture`` siblings (e.g. ``_capture._types``, ``_capture._sweep``) and
# the ``_capture._module_identity`` registry resolve under
# ``python -I -S -B`` with only ``hooks/`` on ``sys.path``.
_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# Register this module under both ``autoskillit.hooks._capture_lifecycle._admission``
# and ``_capture_lifecycle._admission`` so callers from either spelling share
# the same ``sys.modules`` entry. Matches the 2-branch bootstrap in
# ``_store.py`` and the pattern in ``_capture/_authority.py``.
if TYPE_CHECKING:
    from autoskillit.hooks._capture import _module_identity
else:
    _module_identity = importlib.import_module("_capture._module_identity")
_module_identity.register_module_aliases(__name__)

if TYPE_CHECKING:
    from autoskillit.hooks._capture._capacity import admission_reason as _capacity_admission_reason
    from autoskillit.hooks._capture._sweep import OrphanAdoptionOutcome
    from autoskillit.hooks._capture._sweep import (
        scan_and_adopt_orphans as _sweep_scan_and_adopt_orphans,
    )
    from autoskillit.hooks._capture._types import LockContended
else:
    _capture_capacity = importlib.import_module("_capture._capacity")
    _capture_sweep = importlib.import_module("_capture._sweep")
    _capture_types = importlib.import_module("_capture._types")
    _capacity_admission_reason = _capture_capacity.admission_reason
    OrphanAdoptionOutcome = _capture_sweep.OrphanAdoptionOutcome
    _sweep_scan_and_adopt_orphans = _capture_sweep.scan_and_adopt_orphans
    LockContended = _capture_types.LockContended

# Re-exported as ``_capture_lifecycle.MAX_ACTIVE_RECORDS`` via ``__init__.py``.
# Owned here because the active-record cap participates in the admission
# contract (#4440) — the store's ``reserve_capture`` imports it from this
# module so both admission paths share a single source of truth.
MAX_ACTIVE_RECORDS = 4096

# Jittered exponential backoff constants. Moved here from
# ``_capture_lifecycle.py:124-126`` because they are used only by
# ``_acquire_flock``. The original comment block is reproduced verbatim
# from the deleted file.
#
# Jittered exponential backoff for non-blocking lock retry during an active
# sweep: base delay uniformly chosen in [5ms, 20ms], doubling each retry, capped.
# `random` (not `secrets`) is the deliberate choice — its per-process state is
# already seeded from os.urandom at interpreter start, giving OS-entropy jitter
# without the per-call cost of a CSPRNG, and never a wall-clock-derived source.
_LOCK_RETRY_MIN_SECONDS = 0.005
_LOCK_RETRY_MAX_SECONDS = 0.020
_LOCK_RETRY_CAP_SECONDS = 0.25


def _acquire_flock(
    store: Any,
    fd: int,
    *,
    blocking: bool,
) -> None:
    """Acquire ``fd``'s advisory lock, retrying non-blocking contention.

    Blocking callers (the overwhelming majority — every non-sweep
    transition) get a single kernel-blocking ``flock()`` call.
    Non-blocking callers exist only inside an active sweep
    (``store._sweep_budget`` is set for their whole duration): on
    ``EAGAIN``/``EWOULDBLOCK`` they retry with jittered, doubling backoff
    until the *sweep's own* ``max_duration_seconds`` budget — not a new
    knob — is exhausted, then raise ``LockContended``. A non-blocking call
    outside a sweep (should not happen given the current call graph) falls
    back to single-attempt behavior rather than retrying forever.
    """
    operation = fcntl.LOCK_EX
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(fd, operation)
        return
    except OSError as exc:
        if blocking or exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise
        contended = exc
    budget = store._sweep_budget
    started = store._sweep_started_monotonic
    if budget is None or started is None:
        raise LockContended from contended
    deadline = started + budget.max_duration_seconds
    delay = random.uniform(_LOCK_RETRY_MIN_SECONDS, _LOCK_RETRY_MAX_SECONDS)
    while True:
        remaining = deadline - store._monotonic()
        if remaining <= 0:
            raise LockContended from contended
        time.sleep(min(delay, remaining))
        try:
            fcntl.flock(fd, operation)
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            contended = exc
        delay = min(delay * 2.0, _LOCK_RETRY_CAP_SECONDS)


def _admission_reason(
    store: Any,
    records: Mapping[str, Any],
    candidate: Any,
    compaction_epoch: int,
) -> Any:
    """Return the capacity reason a candidate would be refused, or None."""
    return _capacity_admission_reason(
        records,
        candidate,
        compaction_epoch=compaction_epoch,
        spec=store._capacity,
        active_limit=min(MAX_ACTIVE_RECORDS, store._capacity.max_operational_records),
        frame_size_cache=store._capacity_frame_sizes,
    )


def _admit_new_record(
    store: Any,
    record: Any,
    records: dict[str, Any],
    compaction_epoch: int,
    size: int,
) -> bool:
    """Admit a brand-new (never-before-tracked) record if capacity allows.

    Mirrors ``_transition_locked``'s self-accounting: a successful
    admission during an active sweep counts against the same
    ``max_transitions`` budget a state transition does, so
    directory-reconciliation orphan adoption can only ever consume from
    the same active-record capacity real reservations compete for, never
    bypass it (#4440) — capacity-exhausted candidates are silently
    skipped, deferred to a later invocation once cleanup frees room.
    """
    if _admission_reason(store, records, record, compaction_epoch) is not None:
        return False
    store._append_locked(record, records, compaction_epoch, size)
    if store._sweep_budget is not None:
        store._sweep_transitions += 1
    return True


def _scan_and_adopt_orphans(
    store: Any,
    lifecycle_error: Any = None,
) -> OrphanAdoptionOutcome:
    """Run the directory-reconciliation orphan-adoption sweep.

    ``lifecycle_error`` is the exception type the sweep raises when a
    ledger-integrity fault surfaces. It is threaded through as a parameter
    so the wrapper in ``_store.py`` can pass ``CaptureLifecycleError``
    without ``_admission`` needing to import from ``_store`` (which would
    create a circular import at module load time).
    """
    return _sweep_scan_and_adopt_orphans(
        store,
        lifecycle_error=lifecycle_error,
    )
