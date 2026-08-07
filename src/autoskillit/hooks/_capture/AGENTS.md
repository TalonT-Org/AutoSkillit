# hooks/_capture/

Small stdlib-only primitives shared by the shell-capture producer and cleanup
owners. Modules must remain importable when the hooks directory alone is on
`sys.path`.

`_ledger_view.py` owns the validated materialized ledger cache and bounded tail
refresh; the ledger remains the durable source of truth.
`_capacity.py` owns admission classes, projected compacted-byte accounting,
recovery headroom policy, and the capacity-gate declaration: `CapacityGate`
(`ADMISSION`, `TRANSITION`) and `CAPACITY_REASON_GATES`, the total mapping from
every `CaptureCapacityReason` to the gate(s) it can fire from, checked at
import time.
`_migration.py` owns the crash-recoverable legacy publication transaction.
`_reconcile.py` is the only cleanup-owner adapter used by runner-tail and
SessionStart reconciliation.
`_failure_policy.py` owns the failure-disposition registry —
`FAILURE_DISPOSITIONS`, the total mapping from every `CaptureFailureReason` to
`CaptureFailureDisposition.PRESERVE_OUTPUT` or `DISCARD_OUTPUT` — and its
import-time totality guarantee that a reason cannot exist without a declared
disposition. See [ADR-0009](../../../../docs/decisions/0009-verified-output-delivery-disposition.md).
`_lifecycle_policy.py` owns the reclaimability declaration — `ReclaimKind`
(`SWEEP_AFTER_GRACE`, `TOMBSTONE`, `FORENSIC_HOLD`) and `STATE_RECLAIMABILITY`,
the total mapping from every `CaptureState` to its reclaim kind and
grace/hold duration, checked at import time — in addition to the successor
policy it already owned.
