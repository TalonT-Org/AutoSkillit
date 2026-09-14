# pipeline/_audit_admission_ledger/

Persistent authority for audit-admission reservations and their outcomes.
`_schema.py` and `_encoders.py` define the stored record shape; `_authority.py`
and `_connections.py` establish the ledger authority and database access.
`_prepare.py`, `_reservations.py`, `_disposition.py`, and `_finalization.py`
advance an admission from preparation to disposition. `_reads.py` serves
current state, while `_recovery.py` and `_installations.py` restore or install
the ledger when the surrounding pipeline resumes.
