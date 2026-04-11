"""Dependency pin guards (REQ-DEP-001, REQ-DEP-002).

Verifies third-party dependency pins satisfy the audit-derived constraints:
  - pytest is at the latest patched 9.x release
  - igraph has both an explicit lower bound AND an explicit upper bound
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_lock() -> dict:
    return tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text())


def _load_pyproject() -> dict:
    return tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())


def test_pytest_pin_at_or_above_minor() -> None:
    """REQ-DEP-001: pytest must be at the latest patched 9.x release."""
    lock = _load_lock()
    pytest_pkg = next(p for p in lock["package"] if p["name"] == "pytest")
    major, minor, patch = (int(x) for x in pytest_pkg["version"].split("."))
    assert (major, minor) == (9, 0)
    assert patch >= 3, f"pytest must be ≥9.0.3 (got {pytest_pkg['version']})"


def test_igraph_pin_has_explicit_bounds() -> None:
    """REQ-DEP-002: igraph must have an explicit lower bound AND an explicit
    upper bound in pyproject.toml. The unbounded ``>=1.0`` constraint silently
    accepts breaking major bumps."""
    pyproject = _load_pyproject()
    deps = pyproject["project"]["dependencies"]
    igraph_spec = next(
        d
        for d in deps
        if d.split(">")[0].split("<")[0].split("=")[0].strip().lower()
        in {"igraph", "python-igraph"}
    )
    assert ">=" in igraph_spec and "<" in igraph_spec, (
        f"igraph constraint must have explicit upper bound: {igraph_spec!r}"
    )
    lock = _load_lock()
    igraph_pkg = next(p for p in lock["package"] if p["name"] in {"igraph", "python-igraph"})
    assert igraph_pkg["version"], "igraph must be present in lock file"
