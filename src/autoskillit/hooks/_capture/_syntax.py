"""Canonical lexical syntax shared by shell-capture authority boundaries."""

from __future__ import annotations

import re
import sys

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._syntax", "autoskillit.hooks._capture._syntax"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture syntax module identity")

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
