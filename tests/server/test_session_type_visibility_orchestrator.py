"""Orchestrator / skill / food-truck / cook / no-session-type session-type
visibility tests. Companion fleet tests live in
`test_session_type_visibility_fleet.py`.
"""

from __future__ import annotations

import pytest

from autoskillit.core import KITCHEN_GATED_TOOLS

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
async def test_orchestrator_headless_enables_kitchen_tag(monkeypatch):
    from autoskillit.core import (
        EVIDENCE_READER_TOOLS,
        FLEET_DISPATCH_TOOLS,
        FLEET_TOOLS,
        GATED_TOOLS,
    )
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    kitchen_tools = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS - EVIDENCE_READER_TOOLS
    for name in kitchen_tools:
        assert name in tool_names, f"{name} should be visible for orchestrator+headless"
    assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)


@pytest.mark.anyio
async def test_orchestrator_interactive_no_pre_reveal(monkeypatch):
    from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    for name in GATED_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"
    for name in HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"


@pytest.mark.anyio
async def test_skill_headless_enables_headless_tag(monkeypatch):
    from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "test_check" in tool_names, "test_check should be visible for skill+headless"
    assert "post_pr_review" in tool_names
    assert "delegate_evidence_reader" in tool_names
    for name in GATED_TOOLS - HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} (kitchen) should be hidden for skill+headless"


@pytest.mark.anyio
async def test_skill_headless_auto_gate_enables_kitchen_core_and_headless(monkeypatch):
    from autoskillit.core import GATED_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "1")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    tool_tags = {t.name: t.tags for t in tools}
    assert "test_check" in tool_names, "test_check should be visible for skill+headless+auto_gate"
    kitchen_core_tools = {t.name for t in tools if "kitchen-core" in t.tags}
    assert kitchen_core_tools, "kitchen-core-tagged tools should be visible when AUTO_GATE=1"
    visible_gated = {name for name in GATED_TOOLS if name in tool_names}
    assert visible_gated, "At least one GATED_TOOL should be visible via kitchen-core tag"
    for name in visible_gated:
        assert "kitchen-core" in tool_tags[name], f"{name} visible without kitchen-core tag"


@pytest.mark.anyio
async def test_skill_headless_without_auto_gate_only_headless(monkeypatch):
    from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", raising=False)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "test_check" in tool_names, "test_check should be visible for skill+headless"
    for name in GATED_TOOLS - HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} (kitchen) should be hidden for skill+headless"


@pytest.mark.anyio
async def test_skill_headless_auto_gate_zero_only_headless(monkeypatch):
    from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "0")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "test_check" in tool_names, "test_check should be visible for skill+headless+gate=0"
    for name in GATED_TOOLS - HEADLESS_TOOLS:
        assert name not in tool_names, (
            f"{name} (kitchen) should be hidden for skill+headless+gate=0"
        )


@pytest.mark.anyio
async def test_food_truck_with_tool_tags_sees_kitchen_core_plus_declared(monkeypatch):
    """ORCHESTRATOR+HEADLESS with FOOD_TRUCK_TOOL_TAGS sees kitchen-core + github only."""
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}

    assert "run_cmd" in tool_names
    assert "run_skill" in tool_names
    assert "merge_worktree" in tool_names
    assert "fetch_github_issue" in tool_names
    assert "wait_for_ci" not in tool_names
    assert "clone_repo" not in tool_names


@pytest.mark.anyio
async def test_food_truck_with_multiple_packs(monkeypatch):
    """ORCHESTRATOR+HEADLESS with FOOD_TRUCK_TOOL_TAGS=github,ci sees both packs."""
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github,ci")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}

    assert "fetch_github_issue" in tool_names
    assert "wait_for_ci" in tool_names
    assert "clone_repo" not in tool_names


@pytest.mark.anyio
async def test_food_truck_without_tool_tags_sees_full_kitchen(monkeypatch):
    """ORCHESTRATOR+HEADLESS without FOOD_TRUCK_TOOL_TAGS falls back to full kitchen."""
    from autoskillit.core import (
        EVIDENCE_READER_TOOLS,
        FLEET_DISPATCH_TOOLS,
        FLEET_TOOLS,
        GATED_TOOLS,
    )
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", raising=False)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}

    kitchen_tools = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS - EVIDENCE_READER_TOOLS
    for name in kitchen_tools:
        assert name in tool_names
    assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)


@pytest.mark.anyio
async def test_cook_interactive_unaffected_by_tool_tags(monkeypatch):
    """Interactive ORCHESTRATOR (cook) ignores FOOD_TRUCK_TOOL_TAGS."""
    from autoskillit.core import GATED_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}

    for name in GATED_TOOLS:
        assert name not in tool_names


@pytest.mark.anyio
async def test_skill_interactive_no_pre_reveal(monkeypatch):
    from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    for name in GATED_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for skill+interactive"
    for name in HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for skill+interactive"


@pytest.mark.anyio
async def test_transitional_bridge_enables_headless(monkeypatch):
    import warnings

    from autoskillit.core import EVIDENCE_READER_TOOLS, GATED_TOOLS, HEADLESS_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "test_check" in tool_names, "test_check should be visible for bridge HEADLESS=1"
    assert "delegate_evidence_reader" in tool_names
    assert EVIDENCE_READER_TOOLS.isdisjoint(tool_names)
    for name in GATED_TOOLS - HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} (kitchen) should be hidden for bridge"


@pytest.mark.anyio
async def test_fleet_tag_reset_by_conftest(monkeypatch):
    from autoskillit.server import mcp

    # The conftest _reset_mcp_tags fixture has already disabled the fleet tag.
    # Verify: no fleet-enabled state leaked from a previous test.
    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    # No kitchen tools should be visible — fleet tag was reset
    from autoskillit.core import GATED_TOOLS

    for name in GATED_TOOLS:
        assert name not in tool_names, f"{name} should be hidden after conftest reset"


@pytest.mark.anyio
async def test_non_notification_backend_gets_kitchen_pre_reveal(build_ctx, monkeypatch):
    """Non-notification backend gets kitchen tools pre-revealed via lifespan boot."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from autoskillit.core import (
        HEADLESS_ENV_VAR,
    )
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import mcp
    from autoskillit.server._lifespan import _skill_auto_gate_boot

    monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)

    mock_backend = MagicMock()
    mock_backend.capabilities.supports_tool_list_changed = False
    ctx = build_ctx(backend=mock_backend)
    ctx.gate = DefaultGateState(enabled=False)

    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
        with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
            with patch("autoskillit.server._lifespan.register_active_kitchen"):
                await _skill_auto_gate_boot(ctx)

    assert ctx.gate.enabled is True, "gate must be enabled after _skill_auto_gate_boot pre-reveal"

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert KITCHEN_GATED_TOOLS.issubset(tool_names), (
        "All kitchen-tagged gated tools should be visible for non-notification backend"
    )


@pytest.mark.anyio
async def test_non_notification_backend_plan_review_pre_revealed(build_ctx, monkeypatch):
    """Non-notification backend gets plan-review resources pre-revealed via lifespan boot."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from autoskillit.core import HEADLESS_ENV_VAR
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import mcp
    from autoskillit.server._lifespan import _food_truck_auto_gate_boot

    monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)

    mock_backend = MagicMock()
    mock_backend.capabilities.supports_tool_list_changed = False
    ctx = build_ctx(backend=mock_backend)
    ctx.gate = DefaultGateState(enabled=False)

    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
        with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
            with patch("autoskillit.server._lifespan.register_active_kitchen"):
                await _food_truck_auto_gate_boot(ctx)

    assert ctx.gate.enabled is True, (
        "gate must be enabled after _food_truck_auto_gate_boot pre-reveal"
    )

    templates = await mcp.list_resource_templates()
    uris = {t.uri_template for t in templates}
    assert "agent://plan-review/{name}" in uris, (
        "plan-review resource template should be visible for non-notification backend"
    )
