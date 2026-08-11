"""Conservative-fallback liveness.

When an operator disables the page-size override (``page_max_bytes=None``),
recipe-section pagination falls back to the conservative bound computed by
``resolve_recipe_section_response_bound``: ``min(response_max_bytes,
conservative_general_result_limit)`` rather than the wider exemption
ceiling. Claude Code (``recipe_delivery_budget=None``,
``protected_recipe_delivery_capable=False``) always resolves to ENVELOPE for
any bundled recipe whose full surface payload exceeds both the unnegotiated
result limit and the exemption-override admission ceiling -- the fallback
bound must still produce a *working* bounded plan, not crash.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import BoundedDeliveryRoundTripBudgetExceededError
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_RECIPE_PATH = next(path for path in BUNDLED_RECIPE_PATHS if path.stem == "implementation")


def test_claude_conservative_fallback_produces_a_live_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude backend + ``page_max_bytes=None`` must resolve to a usable
    ENVELOPE plan for the bundled ``implementation`` recipe, not raise
    ``BoundedDeliveryRoundTripBudgetExceededError``.
    """
    try:
        envelope = compile_bounded_page_plan(
            _RECIPE_PATH,
            "open_kitchen",
            "claude-code",
            temp_dir=tmp_path,
            monkeypatch=monkeypatch,
            output_budget=OutputBudgetConfig(page_max_bytes=None),
        )
    except BoundedDeliveryRoundTripBudgetExceededError as exc:
        pytest.fail(
            "conservative fallback (page_max_bytes=None) is not live for "
            f"{_RECIPE_PATH.stem}/claude-code -- the fallback bound "
            f"(min(response_max_bytes, conservative_general_result_limit)) "
            f"cannot fit this recipe's flow_records section in a single "
            f"page: {exc}"
        )
    assert envelope.get("success") is True, envelope
    assert envelope.get("delivery_bound_spill") is True, envelope
