"""Calibrated recipe page-bound override."""

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RECIPE_RESPONSE_MAX_UTF8_BYTES, RECIPE_SECTION_RESPONSE_FLOOR_BYTES
from autoskillit.server._recipe_section_pagination import (
    resolve_recipe_section_bound_bytes,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_page_max_bytes_override_bypasses_conservative_token_clamp() -> None:
    assert resolve_recipe_section_bound_bytes(90_000, 46_500, 128_000) == 128_000
    assert resolve_recipe_section_bound_bytes(90_000, 46_500) == 46_500


def test_exemption_ceiling_clamps_override() -> None:
    assert (
        resolve_recipe_section_bound_bytes(
            90_000, 46_500, 200_000, exemption_ceiling_bytes=150_000
        )
        == 150_000
    )


def test_page_max_bytes_rejects_values_below_failure_floor() -> None:
    with pytest.raises(ValueError, match="page_max_bytes"):
        OutputBudgetConfig(page_max_bytes=RECIPE_SECTION_RESPONSE_FLOOR_BYTES - 1)


def test_page_max_bytes_rejects_values_above_recipe_response_ceiling() -> None:
    with pytest.raises(ValueError, match="page_max_bytes"):
        OutputBudgetConfig(page_max_bytes=RECIPE_RESPONSE_MAX_UTF8_BYTES + 1)
