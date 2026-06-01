"""Dependency pin guards (REQ-DEP-001, REQ-DEP-002).

Verifies third-party dependency pins satisfy the audit-derived constraints:
  - pytest is at the latest patched 9.x release
  - networkx has both an explicit lower bound AND an explicit upper bound
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_lock() -> dict[str, Any]:
    return tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text())


def _load_pyproject() -> dict[str, Any]:
    return tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())


def test_pytest_pin_at_or_above_minor() -> None:
    """REQ-DEP-001: pytest must be at the latest patched 9.x release."""
    lock = _load_lock()
    pytest_pkg = next(p for p in lock["package"] if p["name"] == "pytest")
    parts = pytest_pkg["version"].split(".", maxsplit=2)
    major, minor = int(parts[0]), int(parts[1])
    patch_raw = parts[2] if len(parts) > 2 else "0"
    patch = int("".join(c for c in patch_raw.split(".")[0] if c.isdigit()) or "0")
    assert (major, minor) == (9, 0)
    assert patch >= 3, f"pytest must be ≥9.0.3 (got {pytest_pkg['version']})"


def test_networkx_pin_has_explicit_bounds() -> None:
    """REQ-DEP-002: networkx must have an explicit lower bound AND an explicit
    upper bound in pyproject.toml. The unbounded ``>=1.0`` constraint silently
    accepts breaking major bumps."""
    pyproject = _load_pyproject()
    deps = pyproject["project"]["dependencies"]
    networkx_spec = next(
        d
        for d in deps
        if d.split(">")[0].split("<")[0].split("=")[0].strip().lower() == "networkx"
    )
    assert ">=" in networkx_spec and "<" in networkx_spec, (
        f"networkx constraint must have explicit upper bound: {networkx_spec!r}"
    )
