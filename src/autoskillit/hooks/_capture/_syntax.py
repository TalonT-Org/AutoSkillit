"""Canonical lexical syntax shared by shell-capture authority boundaries."""

from __future__ import annotations

import re

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

__all__ = [
    "CAPTURE_ID_RE",
    "INCARNATION_RE",
    "PUBLIC_NAME_PREFIX",
    "PUBLIC_NAME_RE",
    "PUBLIC_NAME_SUFFIX",
    "QUARANTINE_NAME_RE",
    "REFERENCE_RE",
    "SHA256_RE",
    "STAGING_NAME_RE",
]

CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
INCARNATION_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REFERENCE_RE = re.compile(r"^ascr2:([0-9a-f]{16}):([0-9a-f]{32}):([0-9a-f]{64})$")
PUBLIC_NAME_PREFIX = "shell_"
PUBLIC_NAME_SUFFIX = ".log"
PUBLIC_NAME_RE = re.compile(
    rf"^{re.escape(PUBLIC_NAME_PREFIX)}[0-9a-f]{{16}}{re.escape(PUBLIC_NAME_SUFFIX)}$"
)
STAGING_NAME_RE = re.compile(r"^\.capture-staging-[0-9a-f]{16}-[0-9a-f]{16}$")
QUARANTINE_NAME_RE = re.compile(r"^\.capture-quarantine-[0-9a-f]{16}-[0-9a-f]{16}$")
