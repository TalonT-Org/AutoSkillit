"""Fleet per-recipe tool-surface enforcement tests.

Validates that food trucks launched for each bundled recipe see exactly the
expected tool surface under the pack enforcement model: kitchen-core tools
plus only the tools from packs declared in the recipe's requires_packs field.
"""

from __future__ import annotations

import pytest

from tests.fleet._helpers import (
    KITCHEN_CORE_TOOLS,
    TOOLS_BY_PACK,
    compute_food_truck_tool_surface,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

# --- MCP state isolation (module-level; only this file mutates MCP tags) ---


@pytest.fixture(autouse=True)
def _reset_mcp_tags():
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", None)


# ---------------------------------------------------------------------------
# Module-level constants — additional packs used only in this test module
# ---------------------------------------------------------------------------

GITHUB_PACK_TOOLS = TOOLS_BY_PACK["github"]
CI_PACK_TOOLS = TOOLS_BY_PACK["ci"]
CLONE_PACK_TOOLS = TOOLS_BY_PACK["clone"]
TELEMETRY_PACK_TOOLS = TOOLS_BY_PACK["telemetry"]


def _simulate_food_truck(monkeypatch: pytest.MonkeyPatch, packs: str) -> None:
    """Set env vars to simulate a food truck session, then apply visibility."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", packs)
    from autoskillit.server import _apply_session_type_visibility

    _apply_session_type_visibility()


# ---------------------------------------------------------------------------
# Group A: 8 per-recipe tests
# ---------------------------------------------------------------------------

_STANDARD_PACKS = ",".join(sorted(["github", "ci", "clone", "telemetry"]))


class TestPerRecipeToolSurface:
    @pytest.mark.anyio
    async def test_implementation_food_truck_sees_expected_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("implementation")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in expected:
            assert name in visible, f"{name} should be visible for implementation food truck"

    @pytest.mark.anyio
    async def test_implementation_food_truck_hides_research_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import UNGATED_TOOLS
        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("implementation")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        extras = visible - expected - UNGATED_TOOLS
        assert not extras, f"Unexpected tools visible for implementation food truck: {extras}"

    @pytest.mark.anyio
    async def test_merge_prs_food_truck_sees_expected_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("merge-prs")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in expected:
            assert name in visible, f"{name} should be visible for merge-prs food truck"

    @pytest.mark.anyio
    async def test_merge_prs_food_truck_hides_research_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import UNGATED_TOOLS
        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("merge-prs")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        extras = visible - expected - UNGATED_TOOLS
        assert not extras, f"Unexpected tools visible for merge-prs food truck: {extras}"

    @pytest.mark.anyio
    async def test_remediation_food_truck_sees_expected_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("remediation")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in expected:
            assert name in visible, f"{name} should be visible for remediation food truck"

    @pytest.mark.anyio
    async def test_remediation_food_truck_hides_research_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import UNGATED_TOOLS
        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("remediation")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        extras = visible - expected - UNGATED_TOOLS
        assert not extras, f"Unexpected tools visible for remediation food truck: {extras}"

    @pytest.mark.anyio
    async def test_implementation_groups_food_truck_sees_expected_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("implementation-groups")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in expected:
            assert name in visible, (
                f"{name} should be visible for implementation-groups food truck"
            )

    @pytest.mark.anyio
    async def test_implementation_groups_food_truck_hides_research_tools(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import UNGATED_TOOLS
        from autoskillit.server import mcp

        expected = compute_food_truck_tool_surface("implementation-groups")
        _simulate_food_truck(monkeypatch, _STANDARD_PACKS)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        extras = visible - expected - UNGATED_TOOLS
        assert not extras, (
            f"Unexpected tools visible for implementation-groups food truck: {extras}"
        )


# ---------------------------------------------------------------------------
# Group B: 3 synthetic tests
# ---------------------------------------------------------------------------


class TestSyntheticPackScenarios:
    @pytest.mark.anyio
    async def test_food_truck_with_empty_requires_packs_sees_full_kitchen_fallback(
        self, monkeypatch
    ):
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in GATED_TOOLS:
            assert name in visible, (
                f"{name} should be visible under full kitchen fallback (empty L2_TOOL_TAGS)"
            )

    @pytest.mark.anyio
    async def test_food_truck_with_single_pack_sees_kitchen_core_plus_pack(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        _simulate_food_truck(monkeypatch, "github")

        async with Client(mcp) as client:
            tools = await client.list_tools()
        visible = {t.name for t in tools}

        for name in KITCHEN_CORE_TOOLS:
            assert name in visible, f"{name} (kitchen-core) should be visible"
        for name in GITHUB_PACK_TOOLS:
            assert name in visible, f"{name} (github pack) should be visible"
        for name in CI_PACK_TOOLS:
            assert name not in visible, f"{name} (ci pack) should be hidden"

    def test_food_truck_startup_enables_tags_in_order(self, monkeypatch):
        from unittest.mock import MagicMock, call, patch

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "github,ci,telemetry")

        mock_mcp = MagicMock()
        with patch("autoskillit.server.mcp", mock_mcp):
            from autoskillit.server._session_type import _apply_session_type_visibility

            _apply_session_type_visibility()

        enable_calls = mock_mcp.enable.call_args_list
        assert len(enable_calls) == 4, (
            f"Expected 4 enable calls, got {len(enable_calls)}: {enable_calls}"
        )
        assert enable_calls[0] == call(tags={"kitchen-core"}), "kitchen-core must be first"
        assert enable_calls[1] == call(tags={"github"})
        assert enable_calls[2] == call(tags={"ci"})
        assert enable_calls[3] == call(tags={"telemetry"})


# ---------------------------------------------------------------------------
# Group C: 3 regression guards (module-level functions)
# ---------------------------------------------------------------------------


def test_every_bundled_recipe_declares_requires_packs() -> None:
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    MCP_TOOL_PACKS = {"github", "ci", "clone", "telemetry", "kitchen-core"}
    RESEARCH_PACKS = {"research", "exp-lens", "vis-lens"}

    for path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(path)
        assert recipe.requires_packs, f"{path.name} does not declare requires_packs"
        pack_set = set(recipe.requires_packs)
        if pack_set & RESEARCH_PACKS:
            continue
        assert pack_set & MCP_TOOL_PACKS, (
            f"{path.name} declares packs {pack_set} but none are MCP tool packs"
        )


@pytest.mark.anyio
async def test_no_tool_has_bare_kitchen_tag_only() -> None:
    from autoskillit.core._type_constants import CATEGORY_TAGS
    from autoskillit.server import mcp

    all_tools = {t.name: t for t in await mcp.list_tools()}
    for name, tool in all_tools.items():
        if "kitchen" in tool.tags and "autoskillit" in tool.tags:
            pack_tags = tool.tags & CATEGORY_TAGS
            assert pack_tags, (
                f"{name} has 'kitchen' tag but no pack tag from CATEGORY_TAGS. Tags: {tool.tags}"
            )


def test_kitchen_core_and_packs_partition_kitchen_gated_tools() -> None:
    from autoskillit.core._type_constants import TOOL_SUBSET_TAGS

    all_tools_in_subsets = set(TOOL_SUBSET_TAGS.keys())
    union_of_packs: set[str] = set()
    for tools in TOOLS_BY_PACK.values():
        union_of_packs |= tools

    orphaned = all_tools_in_subsets - union_of_packs
    assert not orphaned, f"Tools not in any pack: {orphaned}"
    extra = union_of_packs - all_tools_in_subsets
    assert not extra, f"Tools in packs but not in TOOL_SUBSET_TAGS: {extra}"
