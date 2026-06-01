"""Tests for DRY_WALKTHROUGH_VERIFIED_MARKER constant and ImplementGateConfig integration."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_dry_walkthrough_verified_marker_exists_and_has_correct_value() -> None:
    from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER

    assert isinstance(DRY_WALKTHROUGH_VERIFIED_MARKER, str)
    assert DRY_WALKTHROUGH_VERIFIED_MARKER == "Dry-walkthrough verified = TRUE"


def test_implement_gate_config_marker_references_canonical_constant() -> None:
    from autoskillit.config._config_dataclasses import ImplementGateConfig
    from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER

    assert ImplementGateConfig().marker == DRY_WALKTHROUGH_VERIFIED_MARKER
