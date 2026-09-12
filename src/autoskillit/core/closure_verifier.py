"""Backward-compat shim for closure_verifier — see core.audit.closure_verifier."""

from autoskillit.core.audit.closure_verifier import (
    VerificationResult,
    verify_closure_report,
)

__all__ = ["VerificationResult", "verify_closure_report"]
