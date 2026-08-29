"""Facade for ``_capture_lifecycle`` — preserves the public API surface.

The package was carved out of the 1,209-line ``hooks/_capture_lifecycle.py``
(see issue #4727) so the lock-retry primitive, the capacity-admission
check, and the orphan-adoption call are physically separate from the
``CaptureLifecycleStore`` class. This ``__init__.py`` is a pure re-export
facade — every name in the original ``__all__`` plus the lazy re-aliases
(lines 56–74 of the deleted file) is reproduced verbatim, and every
module-level constant that callers relied on remains importable from
``autoskillit.hooks._capture_lifecycle``.

The three-way discriminator mirrors ``_capture/_reconcile.py:12-32``: under
``TYPE_CHECKING`` (mypy / pyright) the imports use fully-qualified paths
so static analysis can resolve them; under the ``_capture_lifecycle`` bare
package identity (the standalone hook-script path with ``hooks/`` on
``sys.path``) the imports use bare-name siblings; under the regular
``autoskillit.hooks._capture_lifecycle`` package identity they use
relative imports. The same bootstrap block at the top of every submodule
that puts ``hooks/`` on ``sys.path`` lets both runtime branches resolve.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Bootstrap block — mirrors ``_capture/_authority.py`` so that bare-name
# ``_capture`` siblings (e.g. ``_capture._module_identity``) resolve under
# ``python -I -S -B`` with only ``hooks/`` on ``sys.path``.
_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# Register this package under both ``autoskillit.hooks._capture_lifecycle``
# and ``_capture_lifecycle`` so callers from either spelling share the same
# ``sys.modules`` entry. ``_module_identity`` itself is imported via
# ``importlib`` because the bare-name ``_capture`` package has no
# ``__init__.py`` and Pyright cannot statically resolve it.
if TYPE_CHECKING:
    from autoskillit.hooks._capture import _module_identity
else:
    _module_identity = importlib.import_module("_capture._module_identity")
_module_identity.register_module_aliases(__name__)

# Three-way discriminator — re-export the full public API surface from
# ``_store`` (exception classes, ``CaptureLifecycleStore``, ledger-derived
# types, constants) and ``_admission`` (the ``MAX_ACTIVE_RECORDS`` cap that
# participates in the admission contract).
if TYPE_CHECKING:
    from autoskillit.hooks._capture_lifecycle._admission import (
        MAX_ACTIVE_RECORDS as _MAX_ACTIVE_RECORDS,
    )  # noqa: F401
    from autoskillit.hooks._capture_lifecycle._store import (
        _REFERENCE_LIFETIME_SECONDS,
        _RETENTION_SECONDS,
        FRAME_MAGIC,
        LEDGER_NAME,
        LOCK_NAME,
        MAX_LEDGER_BYTES,
        CaptureAuthorityError,
        CaptureCapacityError,
        CaptureCapacityReason,
        CaptureCleanupOutcome,
        CaptureDeliveryStatus,
        CaptureFailureEvidence,
        CaptureFinalManifest,
        CaptureLedgerError,
        CaptureLifecycleError,
        CaptureLifecycleRecord,
        CaptureLifecycleStore,
        CaptureReferenceStatus,
        CaptureRetentionPhase,
        CaptureSnapshotStatus,
        CaptureState,
        CaptureStatus,
        CaptureTransitionCommittedError,
        CaptureWriteAuthority,
        CleanupBlocker,
        CleanupProgress,
        DueKey,
        FinalizedCapture,
        IssuedCaptureReference,
        LegacyCleanupOnly,
        PublishedCaptureReference,
        SweepAttempt,
        SweepBudgetSpec,
        UnavailableCaptureReference,
        VerifiedCaptureSnapshot,
        _CarrierLeaseLive,
        _ObservedArtifact,
    )  # noqa: F401
elif __package__ == "_capture_lifecycle":
    from _capture_lifecycle._admission import MAX_ACTIVE_RECORDS as _MAX_ACTIVE_RECORDS
    from _capture_lifecycle._store import (
        _REFERENCE_LIFETIME_SECONDS,
        _RETENTION_SECONDS,
        FRAME_MAGIC,
        LEDGER_NAME,
        LOCK_NAME,
        MAX_LEDGER_BYTES,
        CaptureAuthorityError,
        CaptureCapacityError,
        CaptureCapacityReason,
        CaptureCleanupOutcome,
        CaptureDeliveryStatus,
        CaptureFailureEvidence,
        CaptureFinalManifest,
        CaptureLedgerError,
        CaptureLifecycleError,
        CaptureLifecycleRecord,
        CaptureLifecycleStore,
        CaptureReferenceStatus,
        CaptureRetentionPhase,
        CaptureSnapshotStatus,
        CaptureState,
        CaptureStatus,
        CaptureTransitionCommittedError,
        CaptureWriteAuthority,
        CleanupBlocker,
        CleanupProgress,
        DueKey,
        FinalizedCapture,
        IssuedCaptureReference,
        LegacyCleanupOnly,
        PublishedCaptureReference,
        SweepAttempt,
        SweepBudgetSpec,
        UnavailableCaptureReference,
        VerifiedCaptureSnapshot,
        _CarrierLeaseLive,
        _ObservedArtifact,
    )  # noqa: F401
else:
    from ._admission import MAX_ACTIVE_RECORDS as _MAX_ACTIVE_RECORDS
    from ._store import (
        _REFERENCE_LIFETIME_SECONDS,
        _RETENTION_SECONDS,
        FRAME_MAGIC,
        LEDGER_NAME,
        LOCK_NAME,
        MAX_LEDGER_BYTES,
        CaptureAuthorityError,
        CaptureCapacityError,
        CaptureCapacityReason,
        CaptureCleanupOutcome,
        CaptureDeliveryStatus,
        CaptureFailureEvidence,
        CaptureFinalManifest,
        CaptureLedgerError,
        CaptureLifecycleError,
        CaptureLifecycleRecord,
        CaptureLifecycleStore,
        CaptureReferenceStatus,
        CaptureRetentionPhase,
        CaptureSnapshotStatus,
        CaptureState,
        CaptureStatus,
        CaptureTransitionCommittedError,
        CaptureWriteAuthority,
        CleanupBlocker,
        CleanupProgress,
        DueKey,
        FinalizedCapture,
        IssuedCaptureReference,
        LegacyCleanupOnly,
        PublishedCaptureReference,
        SweepAttempt,
        SweepBudgetSpec,
        UnavailableCaptureReference,
        VerifiedCaptureSnapshot,
        _CarrierLeaseLive,
        _ObservedArtifact,
    )  # noqa: F401

# Re-bind under the original public-API name. The discriminator imports this
# as ``_MAX_ACTIVE_RECORDS`` to avoid colliding with the same constant in
# ``_admission`` during the package import.
MAX_ACTIVE_RECORDS = _MAX_ACTIVE_RECORDS

# Private re-exports — preserve ``capture_lifecycle._capture_ledger`` /
# ``capture_lifecycle._record_to_dict`` / ``capture_lifecycle.os`` access
# patterns that ``tests/hooks/test_capture_lifecycle.py`` and friends rely
# on. The parent plan's Step 7 (sub-ticket F) replaces these accesses with
# direct ``_capture_lifecycle._store`` / ``_capture_lifecycle._admission``
# bindings; for THIS sub-ticket, re-binding keeps ``task test-check`` green
# without rewriting every test fixture. After sub-ticket F lands, these
# names can be dropped.
if TYPE_CHECKING:
    import os as _os

    from autoskillit.hooks._capture_lifecycle._store import (
        _COMPACTION_THRESHOLD_BYTES,
        _STORE_FACTORY_TOKEN,
        _capture_capacity,
        _capture_ledger,
        _capture_ledger_view,
        _capture_lifecycle_policy,
        _capture_lifecycle_record,
        _capture_migration,
        _capture_resolver,
        _capture_snapshot,
        _capture_sweep,
        _capture_syntax,
        _capture_types,
        _record_from_dict,
        _record_to_dict,
    )  # noqa: F401
elif __package__ == "_capture_lifecycle":
    import os as _os

    from _capture_lifecycle._store import (  # type: ignore[no-redef]
        _COMPACTION_THRESHOLD_BYTES,
        _STORE_FACTORY_TOKEN,
        _capture_capacity,
        _capture_ledger,
        _capture_ledger_view,
        _capture_lifecycle_policy,
        _capture_lifecycle_record,
        _capture_migration,
        _capture_resolver,
        _capture_snapshot,
        _capture_sweep,
        _capture_syntax,
        _capture_types,
        _record_from_dict,
        _record_to_dict,
    )  # noqa: F401
else:
    import os as _os

    from ._store import (
        _COMPACTION_THRESHOLD_BYTES,
        _STORE_FACTORY_TOKEN,
        _capture_capacity,
        _capture_ledger,
        _capture_ledger_view,
        _capture_lifecycle_policy,
        _capture_lifecycle_record,
        _capture_migration,
        _capture_resolver,
        _capture_snapshot,
        _capture_sweep,
        _capture_syntax,
        _capture_types,
        _record_from_dict,
        _record_to_dict,
    )  # noqa: F401

# ``os`` is referenced via ``capture_lifecycle.os`` by some test fixtures;
# rebind it under the original name.
os = _os

__all__ = [
    # Original __all__ (lines 75-92 of the deleted file)
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
    "CaptureAuthorityError",
    "CaptureFailureEvidence",
    "CaptureFinalManifest",
    "CaptureWriteAuthority",
    "DueKey",
    "FinalizedCapture",
    "IssuedCaptureReference",
    "LegacyCleanupOnly",
    "PublishedCaptureReference",
    "SweepAttempt",
    "UnavailableCaptureReference",
    "VerifiedCaptureSnapshot",
    "_CarrierLeaseLive",
    "_ObservedArtifact",
    # Module-level constants
    "FRAME_MAGIC",
    "LEDGER_NAME",
    "LOCK_NAME",
    "MAX_LEDGER_BYTES",
    "MAX_ACTIVE_RECORDS",
    "_RETENTION_SECONDS",
    "_REFERENCE_LIFETIME_SECONDS",
    # Private re-exports — preserved by the ``__init__.py`` for test
    # backward compatibility. Sub-ticket F will retire these entries once
    # the test fixtures stop reaching into ``capture_lifecycle._XXX``.
    "_COMPACTION_THRESHOLD_BYTES",
    "_STORE_FACTORY_TOKEN",
    "_capture_capacity",
    "_capture_ledger",
    "_capture_ledger_view",
    "_capture_lifecycle_policy",
    "_capture_lifecycle_record",
    "_capture_migration",
    "_capture_resolver",
    "_capture_snapshot",
    "_capture_sweep",
    "_capture_syntax",
    "_capture_types",
    "_record_from_dict",
    "_record_to_dict",
]
