"""Private sub-package for the audit admission ledger's per-transition SQL shards.

This package exists so that ``pipeline/audit_admission_ledger.py`` (the public
facade holding ``DefaultAuditAdmissionLedger``) can delegate its per-transition
SQL blocks to small, focused modules without pushing ``pipeline/`` past its
top-level 19-file ceiling. The ``test_no_subpackage_exceeds_10_files`` guard
in ``tests/arch/test_subpackage_isolation.py`` skips nested directories whose
name starts with an underscore, so this package is intentionally private.

The canonical import path for the ledger is unchanged:

    from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger

Consumers should not import anything from this package directly. Shards here
are stateless helpers invoked *inside* the facade's ``BEGIN IMMEDIATE`` /
``COMMIT`` / ``ROLLBACK`` boundary; they do not open connections, hold locks,
or own recovery state.
"""

__all__: list[str] = []
