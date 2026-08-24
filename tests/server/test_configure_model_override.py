"""Runtime configuration and precedence tests for the model override."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from tests.server.test_tools_config import _open_context

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
async def test_configure_order_sets_model_override(tmp_path, monkeypatch) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_order(model_override="opus-recovery"))

    assert payload["success"] is True
    assert ctx.config.model.model_override == "opus-recovery"
    assert payload["config"]["core"]["model_override"] == "opus-recovery"


@pytest.mark.anyio
async def test_configure_fleet_sets_model_override(tmp_path, monkeypatch) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_fleet

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_fleet(model_override="haiku-recovery"))

    assert payload["success"] is True
    assert ctx.config.model.model_override == "haiku-recovery"
    assert payload["config"]["core"]["model_override"] == "haiku-recovery"


@pytest.mark.anyio
@pytest.mark.parametrize("model_override", [" opus-recovery", "opus\nrecovery"])
async def test_configure_rejects_malformed_model_override(
    tmp_path, monkeypatch, model_override
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_order(model_override=model_override))

    assert payload["success"] is False
    assert "printable model identifier without surrounding whitespace" in payload["error"]
    assert ctx.config.model.model_override is None


@pytest.mark.anyio
async def test_model_override_beats_providers_model_overrides(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Ladder A (run_skill dispatch): configure_order(model_override=...) must
    outrank providers.model_overrides[recipe][step], asserted on the
    effective_model handed to the executor."""
    from autoskillit.config.settings import ProvidersConfig
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"implement": "opus"}}
    )
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr(_state, "_ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: False)

    hook_path = tool_ctx_kitchen_open.project_dir / ".autoskillit" / "temp" / ".hook_config.json"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("{}")

    payload = json.loads(await configure_order(model_override="tier1-recovery"))
    assert payload["success"] is True

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="implement")

    assert executor.calls[0].model == "tier1-recovery"


@pytest.mark.anyio
async def test_model_override_beats_config_model_recipe_overrides(tmp_path, monkeypatch) -> None:
    """Ladder B (headless launch): configure_order(model_override=...) must
    outrank model.recipe_overrides[recipe][step] — the #4238 incident-recovery
    proof, asserted on the value AFTER resolve_model_pin runs (not before)."""
    from autoskillit.execution.headless._headless_helpers import (
        resolve_model_identity,
        resolve_model_pin,
    )
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    # A broken per-recipe/per-step override — the #4238 scenario. Set on the
    # baseline (not ctx.config directly): _stage_effective_config rebuilds
    # the candidate config from ctx._baseline_config each call.
    ctx._baseline_config = replace(
        ctx._baseline_config,
        model=replace(
            ctx._baseline_config.model,
            recipe_overrides={"myrecipe": {"mystep": "tier2-broken"}},
        ),
    )
    monkeypatch.setattr(_state, "_ctx", ctx)

    # Without model_override, tier 2 (recipe_overrides) would win.
    baseline_resolution = resolve_model_pin(
        "", ctx._baseline_config, step_name="mystep", recipe_name="myrecipe"
    )
    assert resolve_model_identity(baseline_resolution).configured_model == "tier2-broken"

    payload = json.loads(await configure_order(model_override="tier1-recovery"))
    assert payload["success"] is True

    resolution = resolve_model_pin("", ctx.config, step_name="mystep", recipe_name="myrecipe")
    assert resolve_model_identity(resolution).configured_model == "tier1-recovery"


@pytest.mark.anyio
async def test_model_override_clear_semantics(tmp_path, monkeypatch) -> None:
    """ "" is the explicit clear sentinel, distinguishable from "not supplied"
    (None, the default) — CoreRunConfig.model_override treats both as falsy
    downstream, but the clear must be a genuine, deliberate call."""
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    await configure_order(model_override="opus-recovery")
    assert ctx.config.model.model_override == "opus-recovery"

    # Not supplied: a call with a different param must not disturb it.
    await configure_order(timeout=500)
    assert ctx.config.model.model_override == "opus-recovery"

    # Explicit clear.
    payload = json.loads(await configure_order(model_override=""))
    assert payload["success"] is True
    assert ctx.config.model.model_override == ""
    assert not ctx.config.model.model_override


@pytest.mark.anyio
async def test_close_kitchen_restores_baseline_model_override(tmp_path, monkeypatch) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    ctx = _open_context(tmp_path)
    ctx.reset_visibility = AsyncMock()
    ctx.exploration_context_store = None
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_order(model_override="tier1-recovery"))
    assert payload["success"] is True
    assert ctx.config.model.model_override == "tier1-recovery"

    with patch("autoskillit.server._get_ctx", return_value=ctx):
        with patch("autoskillit.server.tools.tools_kitchen.mcp"):
            result = await close_kitchen(ctx=ctx)

    assert result == "Kitchen is closed."
    assert ctx.config.model.model_override is None
    assert ctx._session_config_overrides == {}
