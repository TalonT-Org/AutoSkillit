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
    "canonical_json_bytes",
    "compute_bytes_hash",
    "compute_canonical_hash",
    "compute_file_hash",
    "compute_request_hash",
    "compute_row_hash",
    "compute_report_hash",
    "parse_canonical_json_bytes",
]

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _reject_noncanonical_value(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("canonical JSON does not permit floating-point values")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical JSON object keys must be strings")
        for item in value.values():
            _reject_noncanonical_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_noncanonical_value(item)
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ValueError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(payload: Any) -> bytes:
    """Encode the narrow AutoSkillit canonical JSON profile."""
    _reject_noncanonical_value(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_canonical_json_bytes(data: bytes) -> Any:
    """Decode canonical JSON, rejecting duplicates, floats, NaN, and noncanonical bytes."""

    def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    def _reject_number(value: str) -> Any:
        raise ValueError(f"canonical JSON does not permit floating-point value {value!r}")

    try:
        text = data.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid canonical JSON: {exc}") from exc
    _reject_noncanonical_value(parsed)
    try:
        encoded = canonical_json_bytes(parsed)
    except UnicodeEncodeError as exc:
        raise ValueError(f"invalid canonical JSON Unicode: {exc}") from exc
    if encoded != data:
        raise ValueError("JSON bytes are not in canonical compact sorted-key form")
    return parsed


def compute_bytes_hash(data: bytes) -> str:
    """Return an algorithm-qualified digest for an already-bounded byte buffer."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def compute_canonical_hash(
    payload: dict[str, Any], *, domain: str = "autoskillit-closure-v1"
) -> str:
    domain_prefix = domain.encode("utf-8") + b"\n"
    digest = hashlib.sha256(domain_prefix + canonical_json_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def compute_file_hash(path: str | Path) -> str:
    return compute_bytes_hash(Path(path).read_bytes())


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
