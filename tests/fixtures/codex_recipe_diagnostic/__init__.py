"""Unsigned diagnostic rollout/trace fixture manifest."""

from __future__ import annotations

from pathlib import Path

DIAGNOSTIC_FIXTURE_SCHEMA_VERSION: int = 1
WRITABLE_ROLLOUT_V1: str = "writable_rollout_v1.jsonl"
UNSIGNED_TRACE_V1: str = "unsigned_trace_v1.jsonl"
DIAGNOSTIC_FIXTURE_NAMES: tuple[str, ...] = (WRITABLE_ROLLOUT_V1, UNSIGNED_TRACE_V1)
DIAGNOSTIC_FIXTURE_COUNT: int = 2


def fixture_path(name: str) -> Path:
    return Path(__file__).parent / name


__all__ = [
    "DIAGNOSTIC_FIXTURE_COUNT",
    "DIAGNOSTIC_FIXTURE_NAMES",
    "DIAGNOSTIC_FIXTURE_SCHEMA_VERSION",
    "UNSIGNED_TRACE_V1",
    "WRITABLE_ROLLOUT_V1",
    "fixture_path",
]
