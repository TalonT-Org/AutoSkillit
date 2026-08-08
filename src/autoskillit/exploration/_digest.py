"""Canonical domain-separated digests for exploration authorities."""

from __future__ import annotations

import hashlib
import json


def canonical_json(value: object) -> str:
    """Return the unique ASCII JSON representation used at identity boundaries."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def qualified_digest(domain: bytes, value: object) -> str:
    """Return a qualified SHA-256 over canonical ASCII JSON and a domain prefix."""

    payload = canonical_json(value).encode("ascii")
    return f"sha256:{hashlib.sha256(domain + payload).hexdigest()}"
