"""Calibrated per-page recipe bounds."""

from pathlib import Path

import pytest

from autoskillit.config import OutputBudgetConfig
from tests.contracts._delivery_constants import (
    MAX_PAGES_PER_SECTION,
    MIN_CALIBRATED_PER_PAGE_BYTES,
)
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda path: path.stem)
def test_per_page_bound_admits_largest_bundled_section_in_one_page(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert OutputBudgetConfig().page_max_bytes is not None
    assert OutputBudgetConfig().page_max_bytes >= MIN_CALIBRATED_PER_PAGE_BYTES
    envelope = compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "codex",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert max(item["total_parts"] for item in envelope["required_sections"]) <= (
        MAX_PAGES_PER_SECTION
    )
