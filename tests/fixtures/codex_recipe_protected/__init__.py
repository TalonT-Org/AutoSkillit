"""Protected-host recipe-delivery fixture manifest."""

from __future__ import annotations

from pathlib import Path

PROTECTED_HOST_FIXTURE_SCHEMA_VERSION: int = 1
PROTECTED_FUNCTIONS_EXEC_V1: str = "protected_functions_exec_v1.json"
PROTECTED_HOST_FIXTURE_NAMES: tuple[str, ...] = (PROTECTED_FUNCTIONS_EXEC_V1,)
PROTECTED_HOST_FIXTURE_COUNT: int = 1


def fixture_path(name: str) -> Path:
    return Path(__file__).parent / name


__all__ = [
    "PROTECTED_FUNCTIONS_EXEC_V1",
    "PROTECTED_HOST_FIXTURE_COUNT",
    "PROTECTED_HOST_FIXTURE_NAMES",
    "PROTECTED_HOST_FIXTURE_SCHEMA_VERSION",
    "fixture_path",
]
