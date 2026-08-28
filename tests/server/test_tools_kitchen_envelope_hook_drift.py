"""Tests for tools_kitchen.py: hook drift warnings and diagnostic warnings."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server._helpers import (
    _configure_admitted_recipe,
    _make_finalized_projection,
    _with_finalized_projection,
)
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group E — hook drift / diagnostic warnings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delivery_mode", ["ordinary_inline", "attested_inline"])
@pytest.mark.anyio
async def test_named_delivery_preserves_finalized_bytes_across_anonymous_guidance_boundary(
    tmp_path,
    monkeypatch,
    delivery_mode,
):
    """Named delivery preserves finalized bytes and never injects sous-chef."""
    from autoskillit.core import RecipeDeliveryDecision, RecipeDeliveryMode
    from autoskillit.server._recipe_delivery import FinalizedRecipeResponse

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "",
    }
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value,
        projection=_make_finalized_projection(),
    )
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []
    finalized = FinalizedRecipeResponse(
        rendered=f"{delivery_mode}:byte-identical",
        decision=RecipeDeliveryDecision(
            mode=RecipeDeliveryMode(delivery_mode),
            caller_requested_outer_tokens=None,
            host_observed_requested_outer_tokens=None,
            required_outer_tokens=1,
            unnegotiated_tool_result_token_limit=10_000,
            selected_result_token_limit=10_000,
            contract_digest="sha256:" + ("0" * 64),
            evidence_identity=None,
            reason="boundary-test",
            producer="open_kitchen",
            payload_sha256="sha256:" + ("1" * 64),
            receipt_status="not_reserved",
        ),
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.finalize_recipe_delivery",
                        return_value=finalized,
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.project_orchestrator_guidance",
                            side_effect=AssertionError(
                                "anonymous guidance crossed named delivery boundary"
                            ),
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            delivered = await open_kitchen(name="demo", ctx=mock_ctx)

    assert delivered.encode() == finalized.rendered.encode()


# T-KITCHEN-1
@pytest.mark.anyio
async def test_open_kitchen_warns_on_orphaned_hooks(tmp_path, monkeypatch):
    """When settings.json contains a hook not in HOOK_REGISTRY, open_kitchen()
    must include a drift warning in its response."""
    from autoskillit.hook_registry import HookDriftResult

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{}")

    monkeypatch.setattr(
        "autoskillit.server._misc._claude_settings_path",
        lambda scope, **_kwargs: settings_dir / "settings.json",
    )
    monkeypatch.setattr(
        "autoskillit.server._misc._count_hook_registry_drift",
        lambda _: HookDriftResult(missing=0, orphaned=1),
    )
    monkeypatch.setattr(
        "autoskillit.server._misc.find_broken_hook_scripts",
        lambda _: [],
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    content = parsed["content"]
    assert (
        "orphan" in content.lower() or "drift" in content.lower() or "install" in content.lower()
    ), "open_kitchen() must include a hook drift warning when orphaned > 0"


# T-KITCHEN-2
@pytest.mark.anyio
async def test_open_kitchen_warns_on_missing_hook_scripts(tmp_path, monkeypatch):
    """When hook scripts are absent from disk, open_kitchen() must warn."""
    from autoskillit.hook_registry import HookDriftResult

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{}")

    monkeypatch.setattr(
        "autoskillit.server._misc._claude_settings_path",
        lambda scope, **_kwargs: settings_dir / "settings.json",
    )
    monkeypatch.setattr(
        "autoskillit.server._misc.find_broken_hook_scripts",
        lambda _: ["python3 /missing/status_health_guard.py"],
    )
    monkeypatch.setattr(
        "autoskillit.server._misc._count_hook_registry_drift",
        lambda _: HookDriftResult(missing=0, orphaned=0),
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    content = parsed["content"]
    assert "Hook scripts not found" in content, (
        "open_kitchen() must include the exact _build_hook_diagnostic_warning phrase"
    )


# T-KITCHEN-3: Site 2 — _build_hook_diagnostic_warning skips missing when plugin marketplace-active
@pytest.mark.anyio
async def test_build_hook_diagnostic_warning_skips_missing_when_plugin_active(
    tmp_path, monkeypatch
):
    """Site 2: _build_hook_diagnostic_warning returns None when plugin is marketplace-installed."""
    from autoskillit.core._plugin_ids import MARKETPLACE_PREFIX

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text('{"hooks": {}}')

    monkeypatch.setattr(
        "autoskillit.server._misc._claude_settings_path",
        lambda scope, **_kwargs: settings_dir / "settings.json",
    )
    monkeypatch.setattr("autoskillit.server._misc.validate_plugin_cache_hooks", lambda **_: [])
    from autoskillit.server._misc import _build_hook_diagnostic_warning

    result = _build_hook_diagnostic_warning(MARKETPLACE_PREFIX)
    assert result is None


# T-KITCHEN-4: Site 2 — orphaned hook detection remains unconditional in MCP path
@pytest.mark.anyio
async def test_build_hook_diagnostic_warning_orphaned_still_fires_when_plugin_active(
    tmp_path, monkeypatch
):
    """Site 2: orphaned detection remains unconditional even when plugin is marketplace-active."""
    from autoskillit.core._plugin_ids import MARKETPLACE_PREFIX
    from autoskillit.hook_registry import HookDriftResult

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text('{"hooks": {}}')

    monkeypatch.setattr(
        "autoskillit.server._misc._claude_settings_path",
        lambda scope, **_kwargs: settings_dir / "settings.json",
    )
    monkeypatch.setattr(
        "autoskillit.server._misc._count_hook_registry_drift",
        lambda _: HookDriftResult(missing=0, orphaned=1),
    )
    monkeypatch.setattr("autoskillit.server._misc.find_broken_hook_scripts", lambda _: [])
    monkeypatch.setattr("autoskillit.server._misc.validate_plugin_cache_hooks", lambda **_: [])
    from autoskillit.server._misc import _build_hook_diagnostic_warning

    result = _build_hook_diagnostic_warning(MARKETPLACE_PREFIX)
    assert result is not None
    assert "orphan" in result.lower()
    assert "1" in result


@pytest.mark.anyio
async def test_prime_quota_cache_catches_typeerror(monkeypatch):
    """_prime_quota_cache must catch TypeError and not propagate — 'fails open' contract."""
    import autoskillit.server._misc as _misc_mod
    from autoskillit.server._misc import _prime_quota_cache

    async def raise_type_error(*a, **kw):
        raise TypeError("float() argument must be a string or a real number, not 'NoneType'")

    monkeypatch.setattr(_misc_mod, "check_and_sleep_if_needed", raise_type_error)

    mock_ctx = MagicMock()
    mock_ctx.config.quota_guard = MagicMock()

    with patch("autoskillit.server._state._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server._misc.logger") as mock_logger:
            # Must not raise — fails open
            await _prime_quota_cache(supports_quota_check=True)
            mock_logger.warning.assert_called_once_with("quota_prime_failed", exc_info=True)
