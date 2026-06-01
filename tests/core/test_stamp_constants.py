"""Tests for DRY_WALKTHROUGH_VERIFIED_MARKER constant."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_dry_walkthrough_verified_marker_exists_and_has_correct_value() -> None:
    from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER

    assert isinstance(DRY_WALKTHROUGH_VERIFIED_MARKER, str)
    assert DRY_WALKTHROUGH_VERIFIED_MARKER == "Dry-walkthrough verified = TRUE"
