"""Canonical lexical syntax shared by shell-capture authority boundaries."""

from __future__ import annotations

import re

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

__all__ = [
    "CAPTURE_ID_RE",
    "INCARNATION_RE",
    "REFERENCE_RE",
    "SHA256_RE",
]

CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
INCARNATION_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REFERENCE_RE = re.compile(r"^ascr2:([0-9a-f]{16}):([0-9a-f]{32}):([0-9a-f]{64})$")
