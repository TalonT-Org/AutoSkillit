"""Constants and byte-bounding helpers shared by more than one shard."""

from __future__ import annotations

_STANDARD_REVIEW_DIMENSIONS = (
    "arch",
    "tests",
    "defense",
    "bugs",
    "cohesion",
    "slop",
)


def _bounded_utf8(value: str, limit: int) -> str:
    """Return a UTF-8 string whose encoded representation is at most ``limit`` bytes."""
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
