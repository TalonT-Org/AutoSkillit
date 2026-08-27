"""Shared constants and byte-bounding helpers for the experimental review pipeline.

Decomposed from ``_experimental_review.py`` per issue #4855.
"""

from __future__ import annotations

from autoskillit.smoke_utils._review_contracts import (
    EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY,
)

_EXPERIMENTAL_DIMENSIONS = dict(EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY)

_MAX_ENVELOPE_ERRORS = 32
_MAX_EXPERIMENTAL_OUTPUT_BYTES = 1024 * 1024

_STANDARD_REVIEW_DIMENSIONS = (
    "arch",
    "tests",
    "defense",
    "bugs",
    "cohesion",
    "slop",
)
_STANDARD_FINDING_KEYS = {
    "file",
    "line",
    "dimension",
    "severity",
    "message",
    "requires_decision",
}
_REVIEW_SEVERITIES = {"critical", "warning", "info"}


def _bounded_utf8(value: str, limit: int) -> str:
    """Return a UTF-8 string whose encoded representation is at most ``limit`` bytes."""
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
