"""Tests for pyproject.toml version lower bounds."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def dev_deps() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text())
    return data["project"]["optional-dependencies"]["dev"]


def test_packaging_lower_bound(dev_deps: list[str]):
    dep = next(d for d in dev_deps if d.startswith("packaging"))
    assert ">=25.0" in dep, f"Expected packaging>=25.0, got: {dep!r}"


def test_pytest_lower_bound(dev_deps: list[str]):
    dep = next(d for d in dev_deps if d.startswith("pytest>") or d.startswith("pytest="))
    assert ">=9.0.0" in dep, f"Expected pytest>=9.0.0, got: {dep!r}"


def test_pytest_asyncio_lower_bound(dev_deps: list[str]):
    dep = next(d for d in dev_deps if d.startswith("pytest-asyncio"))
    assert ">=1.0.0" in dep, f"Expected pytest-asyncio>=1.0.0, got: {dep!r}"


def test_pytest_timeout_no_upper_bound(dev_deps: list[str]):
    dep = next(d for d in dev_deps if d.startswith("pytest-timeout"))
    assert "<2.4" not in dep, f"pytest-timeout should not have <2.4 upper bound: {dep!r}"


def test_ruff_lower_bound(dev_deps: list[str]):
    dep = next(d for d in dev_deps if d.startswith("ruff"))
    assert ">=0.15.0" in dep, f"Expected ruff>=0.15.0, got: {dep!r}"
