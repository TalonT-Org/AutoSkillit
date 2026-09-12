"""Backward-compat shim for closure_hashing — see core.audit.closure_hashing."""

from autoskillit.core.audit.closure_hashing import (
    HASH_RE,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
    parse_canonical_json_bytes,
)

__all__ = [
    "HASH_RE",
    "canonical_json_bytes",
    "compute_bytes_hash",
    "compute_canonical_hash",
    "compute_file_hash",
    "compute_report_hash",
    "compute_request_hash",
    "compute_row_hash",
    "parse_canonical_json_bytes",
]
