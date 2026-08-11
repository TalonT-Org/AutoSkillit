"""Consumed-field wire schema: inline payloads carry only fields with programmatic consumers."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import OutputBudgetConfig
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

# Fields that inline delivery MUST carry (have programmatic consumers)
_CONSUMED_INLINE_FIELDS = frozenset(
    {
        "content",
        "content_hash",
        "composite_hash",
        "errors",
        "flow_records",
        "recipe_execution",
        "recipe_flow",
        "recipe_pull",
        "success",
        "warnings",
    }
)

# Fields that MUST NOT appear (dead weight removed in Stage F)
_EXCLUDED_INLINE_FIELDS = frozenset(
    {
        "finalized_recipe_projection",
    }
)


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_inline_payload_has_no_excluded_fields(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "claude-code",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
        output_budget=OutputBudgetConfig(),
    )
    if envelope.get("delivery_bound_spill") is True:
        pytest.skip("resolves ENVELOPE — no inline fields to check")
    present_excluded = _EXCLUDED_INLINE_FIELDS & set(envelope)
    assert not present_excluded, f"Excluded fields in inline payload: {sorted(present_excluded)}"
    present_consumed = _CONSUMED_INLINE_FIELDS & set(envelope)
    assert present_consumed, f"No consumed fields found in inline payload for {recipe_path.stem}"
