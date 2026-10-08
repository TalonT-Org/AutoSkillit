"""Behavioral contracts for the single kitchen-identity lifecycle owner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("session_type", "headless", "campaign_id", "pre_reveal", "effect_name"),
    [
        pytest.param("orchestrator", "1", None, False, "registry_update", id="food-truck"),
        pytest.param(
            "orchestrator",
            "1",
            "campaign-5250",
            False,
            "registry_update",
            id="food-truck-campaign",
        ),
        pytest.param("fleet", "0", None, False, "registry_update", id="interactive-fleet"),
        pytest.param(
            "orchestrator",
            "0",
            None,
            True,
            "pre_reveal_bootstrap",
            id="interactive-pre-reveal",
        ),
    ],
)
async def test_boot_open_load_share_one_kitchen_identity(
    session_type,
    headless,
    campaign_id,
    pre_reveal,
    effect_name,
    monkeypatch,
    tool_ctx,
):
    """Each boot path keeps its established identity through recipe registration and pull."""
    from autoskillit.core import (
        CAMPAIGN_ID_ENV_VAR,
        DISPATCH_ID_ENV_VAR,
        FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
        SessionType,
    )
    from autoskillit.pipeline import KitchenOpenPhase, get_kitchen_process_identity
    from autoskillit.server import _misc, _tracker_authority
    from autoskillit.server.lifecycle import _lifespan
    from autoskillit.server.lifecycle._lifespan import _LIFESPAN_BOOT_REGISTRY, _session_boots
    from autoskillit.server.tools import tools_kitchen
    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from autoskillit.server.tools.tools_kitchen._open_kitchen import _gate as open_gate
    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server._helpers import _mock_fmcp_ctx
    from tests.server.conftest import _READY_RECIPE_OVERRIDES

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", headless)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", session_type)
    if campaign_id is not None:
        monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, campaign_id)
    if session_type == "orchestrator" and headless == "1":
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")
        monkeypatch.setenv(DISPATCH_ID_ENV_VAR, "disp-5250")
    if pre_reveal:
        from dataclasses import replace

        real_backend = tool_ctx.backend
        assert real_backend is not None
        backend = MagicMock(wraps=real_backend)
        backend.name = real_backend.name
        backend.capabilities = replace(
            real_backend.capabilities,
            supports_tool_list_changed=False,
        )
        tool_ctx.backend = backend

    tool_ctx.quota_refresh_task = None
    overrides = {
        **_READY_RECIPE_OVERRIDES,
        "source_dir": str(tool_ctx.project_dir.resolve()),
    }
    fmcp_ctx = _mock_fmcp_ctx()
    no_op_reaper = AsyncMock()
    no_op_sweep = AsyncMock()
    session_enum = SessionType(session_type)

    with (
        patch.object(_misc, "_prime_quota_cache", new=AsyncMock()),
        patch.object(tools_kitchen, "_prime_quota_cache", new=AsyncMock()),
        patch.object(tools_kitchen, "_write_hook_config"),
        patch.object(_lifespan, "create_background_task", return_value=MagicMock()),
        patch.object(tools_kitchen, "create_background_task", return_value=MagicMock()),
        patch.object(_lifespan, "discover_campaign_state_files", return_value=[]),
        patch.object(tools_kitchen, "discover_campaign_state_files", return_value=[]),
        patch.object(_lifespan, "reap_stale_dispatches_async", new=no_op_reaper),
        patch.object(tools_kitchen, "reap_stale_dispatches_async", new=no_op_reaper),
        patch.object(_lifespan, "sweep_orphaned_tethers_async", new=no_op_sweep),
        patch.object(tools_kitchen, "sweep_orphaned_tethers_async", new=no_op_sweep),
        patch.object(open_gate, "sweep_stale_markers"),
        patch.object(open_gate, "prune_stale_kitchen_state"),
        patch.object(_lifespan, "_reap_self_excluded_codex_and_daemon_orphans"),
        patch.object(_session_boots, "register_active_kitchen", return_value=True),
        patch.object(_tracker_authority, "register_active_kitchen", return_value=True),
    ):
        await _LIFESPAN_BOOT_REGISTRY[session_enum](tool_ctx)

        opened = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
        loaded = json.loads(await load_recipe(name="research", overrides=overrides))

    assert opened["success"] is True, opened
    assert tool_ctx.kitchen_open_state.phase is KitchenOpenPhase.COMMITTED
    assert loaded["success"] is True, loaded
    assert tool_ctx.kitchen_id == tool_ctx.kitchen_open_state.kitchen_id
    assert get_kitchen_process_identity(tool_ctx).kitchen_id == tool_ctx.kitchen_id
    effect = next(item for item in tool_ctx.kitchen_open_state.effects if item.name == effect_name)
    assert effect.downstream_identity == tool_ctx.kitchen_id
    if campaign_id is not None:
        assert tool_ctx.kitchen_id == campaign_id


def test_establish_kitchen_identity_activates_and_resets_owner_cache(tool_ctx):
    from autoskillit.pipeline import KitchenOpenPhase, get_kitchen_process_identity
    from autoskillit.server.lifecycle._kitchen_identity import establish_kitchen_identity
    from autoskillit.server.recipe._recipe_generation import get_recipe_generation_store
    from tests.server.test_recipe_generation import _record

    store = get_recipe_generation_store()
    store.activate_kitchen("other")
    owner_identity = get_kitchen_process_identity(tool_ctx, "owner-before-establishment")
    assert owner_identity.kitchen_id == "owner-before-establishment"

    kitchen_id = establish_kitchen_identity(tool_ctx)

    assert kitchen_id
    assert tool_ctx.kitchen_open_state.phase is KitchenOpenPhase.INFRASTRUCTURE_READY
    assert tool_ctx.kitchen_open_state.kitchen_id == kitchen_id
    assert tool_ctx.kitchen_process_identity is None
    stored = store.put(_record(kitchen_id=kitchen_id, compile_key=f"compile-{kitchen_id}"))
    assert stored.kitchen_id == kitchen_id


@pytest.mark.parametrize("source", ["campaign", "resolver"])
def test_establish_kitchen_identity_rejects_whitespace_before_activation(
    tool_ctx, monkeypatch, source
):
    from autoskillit.server.lifecycle import _kitchen_identity
    from autoskillit.server.recipe import _recipe_generation

    activate = Mock()
    monkeypatch.setattr(_recipe_generation, "activate_kitchen", activate)
    monkeypatch.setattr(_kitchen_identity, "resolve_kitchen_id", lambda: " \t\n")
    state = tool_ctx.kitchen_open_state
    cached_identity = tool_ctx.kitchen_process_identity

    with pytest.raises(ValueError, match="non-whitespace"):
        _kitchen_identity.establish_kitchen_identity(
            tool_ctx, campaign_id=" \t\n" if source == "campaign" else None
        )

    activate.assert_not_called()
    assert tool_ctx.kitchen_open_state is state
    assert tool_ctx.kitchen_process_identity is cached_identity


def test_establish_kitchen_identity_is_idempotent_and_preserves_current_cache(
    tool_ctx, monkeypatch
):
    from autoskillit.pipeline import get_kitchen_process_identity
    from autoskillit.server.lifecycle import _kitchen_identity

    establish = _kitchen_identity.establish_kitchen_identity
    kitchen_id = establish(tool_ctx)
    cached_identity = get_kitchen_process_identity(tool_ctx)
    resolver_spy = Mock(wraps=_kitchen_identity.resolve_kitchen_id)
    monkeypatch.setattr(_kitchen_identity, "resolve_kitchen_id", resolver_spy)

    assert establish(tool_ctx) == kitchen_id
    resolver_spy.assert_not_called()
    assert tool_ctx.kitchen_process_identity is cached_identity
