"""Canonical hash utilities for closure-mode verdict verification (IL-0, stdlib-only).

Domain-separated SHA-256 hashing for audit-impl closure reports. Every hash
returned uses the ``sha256:<64-lowercase-hex>`` prefix format, matching the
majority convention in ``recipe/identity.py`` and ``recipe/staleness_cache.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "HASH_RE",
    "compute_canonical_hash",
    "compute_file_hash",
    "compute_request_hash",
    "compute_row_hash",
    "compute_report_hash",
]

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_canonical_hash(
    payload: dict[str, Any], *, domain: str = "autoskillit-closure-v1"
) -> str:
    domain_prefix = domain.encode("utf-8") + b"\n"
    digest = hashlib.sha256(domain_prefix + _canonical_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def compute_file_hash(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def compute_request_hash(
    authority_hash: str,
    plan_hashes: list[str],
    base_sha: str,
    diff_sha: str,
    target_sha: str,
) -> str:
    payload = {
        "authority_hash": authority_hash,
        "base_sha": base_sha,
        "diff_sha": diff_sha,
        "plan_hashes": plan_hashes,
        "target_sha": target_sha,
    }
    return compute_canonical_hash(payload)


def compute_row_hash(
    requirement_id: str,
    requirement_text: str,
    assessment: str,
    evidence_summary: str,
    source_file: str = "",
    source_line: int = 0,
    source_section: str = "",
) -> str:
    payload = {
        "assessment": assessment,
        "evidence_summary": evidence_summary,
        "requirement_id": requirement_id,
        "requirement_text": requirement_text,
        "source_file": source_file,
        "source_line": source_line,
        "source_section": source_section,
    }
    return compute_canonical_hash(payload)


def compute_report_hash(request_hash: str, row_hashes: list[str], verdict: str) -> str:
    payload = {
        "request_hash": request_hash,
        "row_hashes": row_hashes,
        "verdict": verdict,
    }
    return compute_canonical_hash(payload)
