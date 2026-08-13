"""Unit tests for the reconciled recipe-section bound resolver."""

from __future__ import annotations

import pytest

from autoskillit.core._delivery_bounds import resolve_recipe_section_response_bound

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_override_is_clamped_by_exemption_ceiling() -> None:
    """An override is an input to reconciliation, never a bypass."""
    result = resolve_recipe_section_response_bound(
        response_max_bytes=90_000,
        conservative_general_result_limit=46_500,
        page_max_bytes_override=195_000,
        exemption_ceiling_bytes=150_000,
    )
    assert result == 150_000


def test_override_without_ceiling_passes_through() -> None:
    """Without an exemption ceiling, the override passes through verbatim."""
    result = resolve_recipe_section_response_bound(
        response_max_bytes=90_000,
        conservative_general_result_limit=46_500,
        page_max_bytes_override=195_000,
    )
    assert result == 195_000


def test_no_override_uses_min_of_response_and_conservative() -> None:
    """Without an override, behavior matches the existing min() policy."""
    result = resolve_recipe_section_response_bound(
        response_max_bytes=90_000,
        conservative_general_result_limit=46_500,
    )
    assert result == 46_500


def test_no_override_with_ceiling_clamps_min() -> None:
    """Without an override, the min() result is still clamped to the ceiling."""
    result = resolve_recipe_section_response_bound(
        response_max_bytes=90_000,
        conservative_general_result_limit=46_500,
        exemption_ceiling_bytes=30_000,
    )
    assert result == 30_000


def test_override_smaller_than_ceiling_passes_through() -> None:
    """When the override is already within the ceiling, it passes through."""
    result = resolve_recipe_section_response_bound(
        response_max_bytes=90_000,
        conservative_general_result_limit=46_500,
        page_max_bytes_override=100_000,
        exemption_ceiling_bytes=195_000,
    )
    assert result == 100_000
