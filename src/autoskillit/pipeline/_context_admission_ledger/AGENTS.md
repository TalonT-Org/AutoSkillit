# pipeline/_context_admission_ledger/

Crash-safe context-admission accounting for pipeline sessions.
`_store.py`, `_storage.py`, and `_codec.py` own persisted records and their
encoding. `_shadow.py`, `_apply.py`, and `_projection.py` maintain the shadow
balance and apply accepted transitions. `_inspection.py`, `_state_queries.py`,
and `_status.py` expose the current admission view. `_recover.py` restores the
ledger after interrupted writes; `_sqlite_errors.py` classifies storage errors.
