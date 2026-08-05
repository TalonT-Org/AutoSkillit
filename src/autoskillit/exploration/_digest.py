"""Canonical domain-separated digests for exploration authorities."""

from __future__ import annotations

import hashlib
import json


def qualified_digest(domain: bytes, value: object) -> str:
    """Return a qualified SHA-256 over canonical ASCII JSON and a domain prefix."""

    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(domain + payload).hexdigest()}"
