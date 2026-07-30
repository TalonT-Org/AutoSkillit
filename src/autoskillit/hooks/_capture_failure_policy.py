"""Canonical persisted and transported capture-failure field policy."""

from __future__ import annotations

import re
import sys

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture_failure_policy", "autoskillit.hooks._capture_failure_policy"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture failure-policy module identity")

FAILURE_STAGE_MAX_BYTES = 64
FAILURE_DETAIL_MAX_BYTES = 240
FAILURE_STAGE_RE = re.compile(rf"^[a-z][a-z0-9_]{{0,{FAILURE_STAGE_MAX_BYTES - 1}}}$")


def normalize_failure_stage(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in value.lower()
    ).strip("_")[:FAILURE_STAGE_MAX_BYTES]
    return normalized if FAILURE_STAGE_RE.fullmatch(normalized) else "capture_failure"


def normalize_failure_detail(value: str) -> str:
    normalized = " ".join(value.split()) or "capture failure"
    return normalized.encode("utf-8")[:FAILURE_DETAIL_MAX_BYTES].decode(
        "utf-8",
        errors="ignore",
    )


def valid_failure_stage(value: object) -> bool:
    return isinstance(value, str) and FAILURE_STAGE_RE.fullmatch(value) is not None


def valid_failure_detail(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= FAILURE_DETAIL_MAX_BYTES
    )
