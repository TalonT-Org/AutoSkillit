"""Dependency pin guards (REQ-DEP-001, REQ-DEP-002)."""

from __future__ import annotations

import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_lock() -> dict:
    return tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text())


def test_pytest_pin_at_or_above_minor() -> None:
    """REQ-DEP-001: pytest must be at the latest patched 9.x release."""
    lock = _load_lock()
    pytest_pkg = next(p for p in lock["package"] if p["name"] == "pytest")
    major, minor, patch = (int(x) for x in pytest_pkg["version"].split("."))
    assert (major, minor) == (9, 0)
    assert patch >= 3, f"pytest must be ≥9.0.3 (got {pytest_pkg['version']})"
