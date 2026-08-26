"""Private sub-package for the audit admission ledger's per-transition SQL shards.

The canonical import path is ``autoskillit.pipeline.audit_admission_ledger``.
Shards here are stateless helpers invoked *inside* the facade's
``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK`` boundary.
"""

__all__: list[str] = []
