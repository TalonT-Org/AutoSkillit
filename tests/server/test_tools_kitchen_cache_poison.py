"""Cross-tool cache-poison regression: open_kitchen(ingredients_only=True) must not corrupt
subsequent load_recipe calls for the same recipe.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def test_open_kitchen_ingredients_only_does_not_poison_load_recipe(
    tool_ctx_kitchen_open,
    monkeypatch,
):
    """open_kitchen(ingredients_only=True) must not corrupt subsequent load_recipe."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from autoskillit.server.tools.tools_recipe import load_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    with patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()):
        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server.tools.tools_kitchen.create_background_task"):
                with patch(
                    "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                    return_value="test-kitchen",
                ):
                    ok_result = json.loads(
                        await open_kitchen(
                            name="implementation",
                            ingredients_only=True,
                            ctx=tool_ctx_kitchen_open,
                        )
                    )

    assert "content" not in ok_result, (
        f"open_kitchen(ingredients_only=True) must not include 'content' in response, "
        f"got keys: {list(ok_result.keys())}"
    )

    lr_result = json.loads(await load_recipe(name="implementation"))
    assert "content" in lr_result, (
        "load_recipe must return 'content' after open_kitchen(ingredients_only=True); "
        "cache was poisoned by the ingredients_only pop"
    )
    assert isinstance(lr_result["content"], str)
    assert len(lr_result["content"]) > 0
