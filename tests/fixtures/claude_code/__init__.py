"""Content-free Claude Code stdout conformance fixtures."""

from __future__ import annotations

from pathlib import Path

WEEKLY_RATE_LIMIT_REJECTED_V1 = "weekly_rate_limit_rejected_v1.jsonl"
API_ERROR_404_TERMINAL_V1 = "api_error_404_terminal_v1.jsonl"
AUTHENTICATION_FAILED_V1 = "authentication_failed_v1.jsonl"

ALL_FIXTURE_NAMES = (
    WEEKLY_RATE_LIMIT_REJECTED_V1,
    API_ERROR_404_TERMINAL_V1,
    AUTHENTICATION_FAILED_V1,
)


def fixture_path(name: str) -> Path:
    """Return the absolute path to a fixture file in this directory."""
    return Path(__file__).parent / name


__all__ = [
    "ALL_FIXTURE_NAMES",
    "API_ERROR_404_TERMINAL_V1",
    "AUTHENTICATION_FAILED_V1",
    "WEEKLY_RATE_LIMIT_REJECTED_V1",
    "fixture_path",
]
