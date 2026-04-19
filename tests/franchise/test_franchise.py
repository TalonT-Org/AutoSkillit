"""Tests for franchise package."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small]


def test_franchise_package_importable() -> None:
    """franchise package can be imported without error."""
    import autoskillit.franchise  # noqa: F401
