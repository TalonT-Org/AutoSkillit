"""End-to-end schema consistency tests for the pretty_output hook.

Tests that real tool handler output piped through the hook formatter
stays consistent (prevents silent schema drift).
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# PHK-E1/E2: End-to-end schema consistency tests
# ---------------------------------------------------------------------------


class TestFormatterSchemaConsistency:
    """End-to-end tests: real tool handler output piped through hook formatter.

    Prevents silent schema drift between tool handlers and formatters.

    The `tool_ctx` fixture (from tests/conftest.py) monkeypatches `server._ctx`,
    making `list_recipes()` and `kitchen_status()` use the test ToolContext.
    Neither function accepts a `ctx` argument — they resolve context via `_get_ctx()`.
    """

    @pytest.mark.anyio
    async def test_list_recipes_tool_output_through_hook(self, tool_ctx):
        """PHK-E1: list_recipes real output contains all recipe names through hook."""
        from autoskillit.hooks.pretty_output import _format_response
        from autoskillit.server.tools_recipe import list_recipes

        result_json = await list_recipes()
        output = _format_response("mcp__autoskillit__list_recipes", result_json, pipeline=False)

        data = json.loads(result_json)
        for recipe in data.get("recipes", []):
            assert recipe["name"] in output, (
                f"Recipe '{recipe['name']}' missing from formatted output — possible truncation"
            )

    @pytest.mark.anyio
    async def test_kitchen_status_tool_output_through_hook(self, tool_ctx):
        """PHK-E2: kitchen_status real output contains all key fields through hook."""
        from autoskillit.hooks.pretty_output import _format_response
        from autoskillit.server.tools_status import kitchen_status

        result_json = await kitchen_status()
        output = _format_response("mcp__autoskillit__kitchen_status", result_json, pipeline=False)

        data = json.loads(result_json)
        for key in ("package_version", "tools_enabled"):
            assert str(data[key]) in output, (
                f"Field '{key}' value missing from formatted kitchen_status output"
            )
