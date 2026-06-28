"""Codex NDJSON deterministic conformance fixture schemas.

Versions the sealed JSON Schema fixtures for B3a conformance testing.
FIXTURE_SCHEMA_VERSION tracks the JSON Schema fixture format and is distinct
from CODEX_SCHEMA_VERSION (in tests/fixtures/codex/ and core/types/) which
versions the NDJSON session replay fixture format.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_SCHEMA_VERSION: int = 1


def fixture_schema_path(name: str) -> Path:
    """Return the absolute path to a fixture file in this directory."""
    return Path(__file__).parent / name


__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "fixture_schema_path",
]
