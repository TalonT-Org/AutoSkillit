"""Crash-safe SQLite storage for shadow context-admission accounting.

The implementation lives in the private subpackage
``autoskillit.pipeline._context_admission_ledger``; this module is the
stable public facade re-exporting the implementation class. See Wavefront 1
of #4667.
"""

from autoskillit.pipeline._context_admission_ledger import DefaultContextAdmissionLedger

__all__ = ["DefaultContextAdmissionLedger"]
