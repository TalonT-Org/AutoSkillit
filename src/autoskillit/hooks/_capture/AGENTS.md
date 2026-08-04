# hooks/_capture/

Small stdlib-only primitives shared by the shell-capture producer and cleanup
owners. Modules must remain importable when the hooks directory alone is on
`sys.path`.

`_ledger_view.py` owns the validated materialized ledger cache and bounded tail
refresh; the ledger remains the durable source of truth.
`_capacity.py` owns admission classes, projected compacted-byte accounting, and
recovery headroom policy.
`_migration.py` owns the crash-recoverable legacy publication transaction.
`_reconcile.py` is the only cleanup-owner adapter used by runner-tail and
SessionStart reconciliation.
