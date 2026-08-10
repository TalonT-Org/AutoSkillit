"""Session-start MCP round-trip budgets."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from tests.contracts._delivery_constants import (
    MAX_OPEN_KITCHEN_CALLS,
)
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS
from tests.server._helpers import simulate_session_start

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium, pytest.mark.anyio]

_RECIPE_NAMES = tuple(path.stem for path in BUNDLED_RECIPE_PATHS)


@pytest.mark.parametrize("recipe_name", _RECIPE_NAMES, ids=lambda name: name)
@pytest.mark.parametrize("backend_name", sorted(BACKEND_REGISTRY), ids=lambda name: name)
async def test_session_start_round_trip_count_is_bounded(
    recipe_name: str,
    backend_name: str,
    tool_ctx: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = await simulate_session_start(
        recipe_name,
        backend_name,
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    mode = (
        "codex_bounded"
        if backend_name == "codex"
        else ("claude_code_inline" if len(counter) == 1 else "claude_code_bounded")
    )
    assert len(counter) <= MAX_OPEN_KITCHEN_CALLS[mode]
    if len(counter) > 1:
        assert all(tool_name != "complete_recipe_initialization" for tool_name, _ in counter.calls)
