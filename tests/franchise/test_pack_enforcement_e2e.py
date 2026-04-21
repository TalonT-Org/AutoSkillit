"""Franchise per-recipe tool-surface e2e tests.

Validates the tool surface using a real MCP server subprocess — no monkeypatching.
Marked integration + medium to allow subprocess spawning.
"""

from __future__ import annotations

import os
import sys

import pytest

from autoskillit.core._type_constants import PACK_REGISTRY, TOOL_SUBSET_TAGS

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Module-level constants (mirrors test_pack_enforcement.py)
# ---------------------------------------------------------------------------

_tools_by_pack: dict[str, set[str]] = {}
for _tool, _tags in TOOL_SUBSET_TAGS.items():
    for _tag in _tags:
        if _tag in PACK_REGISTRY:
            _tools_by_pack.setdefault(_tag, set()).add(_tool)

TOOLS_BY_PACK: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in _tools_by_pack.items()}

KITCHEN_CORE_TOOLS = TOOLS_BY_PACK["kitchen-core"]


def compute_food_truck_tool_surface(recipe_name: str) -> frozenset[str]:
    """Compute the expected tool surface for a food truck running the given recipe."""
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    path = builtin_recipes_dir() / f"{recipe_name}.yaml"
    recipe = load_recipe(path)
    expected: set[str] = set(KITCHEN_CORE_TOOLS)
    for pack in recipe.requires_packs or []:
        expected |= TOOLS_BY_PACK.get(pack, frozenset())
    return frozenset(expected)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def franchise_runtime():
    async def _get_surface(recipe_name: str) -> set[str]:
        from fastmcp.client import Client
        from fastmcp.client.transports import StdioTransport

        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        path = builtin_recipes_dir() / f"{recipe_name}.yaml"
        recipe = load_recipe(path)
        packs = ",".join(sorted(recipe.requires_packs))

        env = {
            **os.environ,
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_L2_TOOL_TAGS": packs,
        }

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "autoskillit"],
            env=env,
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
        return {t.name for t in tools}

    return _get_surface


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_implementation_food_truck_real_subprocess_tool_surface(franchise_runtime):
    from autoskillit.core import UNGATED_TOOLS

    visible = await franchise_runtime("implementation")
    expected = compute_food_truck_tool_surface("implementation")

    extras = visible - expected - UNGATED_TOOLS
    assert not extras, f"Unexpected tools visible for implementation food truck: {extras}"
    for name in expected:
        assert name in visible, f"{name} should be visible for implementation food truck"


@pytest.mark.integration
@pytest.mark.anyio
async def test_merge_prs_food_truck_real_subprocess_tool_surface(franchise_runtime):
    from autoskillit.core import UNGATED_TOOLS

    visible = await franchise_runtime("merge-prs")
    expected = compute_food_truck_tool_surface("merge-prs")

    extras = visible - expected - UNGATED_TOOLS
    assert not extras, f"Unexpected tools visible for merge-prs food truck: {extras}"
    for name in expected:
        assert name in visible, f"{name} should be visible for merge-prs food truck"
