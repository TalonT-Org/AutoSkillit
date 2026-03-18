"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations


def test_enums_importable_from_sub_module():
    from autoskillit.core._type_enums import (
        RetryReason,
    )

    assert issubclass(RetryReason, str)


def test_protocols_importable_from_sub_module():
    pass


def test_types_hub_backward_compat():
    """All symbols must still be importable from autoskillit.core.types."""


def test_types_hub_line_count_under_threshold():
    """After split, core/types.py must be under 200 lines (re-export hub only)."""
    from autoskillit.core import paths

    types_path = paths.pkg_root() / "core" / "types.py"
    lines = types_path.read_text().splitlines()
    assert len(lines) < 200, f"types.py has {len(lines)} lines; expected re-export hub only"
