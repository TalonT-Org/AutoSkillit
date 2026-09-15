"""Tests for INVESTIGATION_COMPLETE_MARKER."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# ---------------------------------------------------------------------------
# INVESTIGATION_COMPLETE_MARKER constant (T1)
# ---------------------------------------------------------------------------


def test_investigation_complete_marker_defined() -> None:
    """INVESTIGATION_COMPLETE_MARKER must be defined and exported from autoskillit.core."""
    from autoskillit.core import INVESTIGATION_COMPLETE_MARKER

    assert INVESTIGATION_COMPLETE_MARKER == "<!-- investigation_complete: true -->"


def test_investigation_complete_marker_in_all() -> None:
    """INVESTIGATION_COMPLETE_MARKER must be in _type_constants.__all__."""
    from autoskillit.core.types import _type_constants

    assert "INVESTIGATION_COMPLETE_MARKER" in _type_constants.__all__  # type: ignore[attr-defined]
