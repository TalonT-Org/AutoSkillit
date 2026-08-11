"""Default-config four-call efficiency pin.

Replaces the runtime invariant (``_MAX_BOUNDED_RECIPE_CALLS = 4`` in
``server/_recipe_delivery.py``) with an explicit, non-negotiable contract:
under the default ``OutputBudgetConfig``, every bundled recipe's Codex
ENVELOPE plan (Codex always forces the bounded path) must complete a full
session-start round trip in at most 4 MCP calls -- one ``open_kitchen``, up
to one ``get_recipe_section`` page per required section, and one
``complete_recipe_initialization``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import OutputBudgetConfig
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_MAX_BOUNDED_RECIPE_CALLS = 4


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda path: path.stem)
def test_bundled_recipe_default_config_codex_envelope_is_within_four_calls(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "codex",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
        output_budget=OutputBudgetConfig(),
    )
    assert envelope.get("delivery_bound_spill") is True, envelope
    required_sections = envelope["required_sections"]
    planned_calls = 1 + sum(item["total_parts"] for item in required_sections) + 1
    assert planned_calls <= _MAX_BOUNDED_RECIPE_CALLS, (
        f"{recipe_path.stem}: default-config Codex envelope needs {planned_calls} "
        f"calls, exceeding the pinned {_MAX_BOUNDED_RECIPE_CALLS}-call budget"
    )
