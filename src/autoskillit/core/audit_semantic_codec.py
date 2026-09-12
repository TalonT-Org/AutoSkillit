"""Backward-compat shim for audit_semantic_codec — see core.audit.audit_semantic_codec."""

from autoskillit.core.audit.audit_semantic_codec import (
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    AuditSemanticCodecError,
    canonical_full_reference_records_match,
    load_audit_semantic_result,
    load_standalone_audit_evidence,
)

__all__ = [
    "AUDIT_SEMANTIC_SCHEMA_VERSION",
    "AuditSemanticCodecError",
    "canonical_full_reference_records_match",
    "load_audit_semantic_result",
    "load_standalone_audit_evidence",
]
