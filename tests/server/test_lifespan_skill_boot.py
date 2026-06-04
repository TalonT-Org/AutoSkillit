"""Tests for skill lifespan auto-gate boot and registry wiring."""

from __future__ import annotations

import pytest
import structlog.testing

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.feature("skill")
class TestSkillAutoGateBoot:
    """Skill lifespan auto-gate: _skill_auto_gate_boot opens gate."""

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_opens_gate(self, tool_ctx, monkeypatch):
        """SKILL+HEADLESS+AUTO_GATE: gate is open after _skill_auto_gate_boot() runs."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True
        assert tool_ctx.kitchen_id is not None
        assert tool_ctx.active_recipe_packs == frozenset()
        assert tool_ctx.active_recipe_features == frozenset()

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_noop_when_headless_not_1(self, tool_ctx, monkeypatch):
        """SKILL without HEADLESS=1: gate remains closed (notification-capable backend)."""
        from unittest.mock import MagicMock

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = True
        tool_ctx.backend = mock_backend

        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_noop_when_auto_gate_not_1(self, tool_ctx, monkeypatch):
        """HEADLESS=1 without HEADLESS_AUTO_GATE=1: gate remains closed."""
        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.delenv(HEADLESS_AUTO_GATE_ENV_VAR, raising=False)

        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_warns_and_returns_when_no_gate(
        self, tool_ctx, monkeypatch
    ):
        """gate is None: warning logged and early return (gate stays closed)."""
        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = None
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with structlog.testing.capture_logs() as logs:
            await _skill_auto_gate_boot(tool_ctx)

        assert any(entry.get("event") == "skill_auto_gate_boot_no_gate" for entry in logs)
        assert tool_ctx.kitchen_id is not None

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_feature_suppression_error(
        self, tool_ctx, monkeypatch
    ):
        """Feature suppression failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.server._lifespan._collect_disabled_feature_tags",
            side_effect=RuntimeError("feature error"),
        ):
            with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_hook_config_error(
        self, tool_ctx, monkeypatch
    ):
        """_write_hook_config failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config",
            side_effect=OSError("disk full"),
        ):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_quota_cache_error(
        self, tool_ctx, monkeypatch
    ):
        """_prime_quota_cache failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch(
                "autoskillit.server._misc._prime_quota_cache",
                new=AsyncMock(side_effect=RuntimeError("quota cache error")),
            ):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_registry_error(self, tool_ctx, monkeypatch):
        """register_active_kitchen failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.register_active_kitchen",
                    side_effect=OSError("registry write error"),
                ):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_no_quota_refresh_loop(self, tool_ctx, monkeypatch):
        """_skill_auto_gate_boot does NOT create a quota_refresh_loop background task."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ) as mock_create_bg_task:
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _skill_auto_gate_boot(tool_ctx)

        quota_loop_calls = [
            call
            for call in mock_create_bg_task.call_args_list
            if call.kwargs.get("label") == "quota_refresh_loop"
        ]
        assert len(quota_loop_calls) == 0, (
            "quota_refresh_loop must not be created for SKILL sessions"
        )

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_uses_project_dir_for_registration(
        self, build_ctx, tmp_path, monkeypatch
    ):
        """register_active_kitchen must be called with ctx.project_dir, not Path.cwd()."""
        import os
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        monkeypatch.chdir(tmp_path)
        different_dir = tmp_path / "project_root"
        different_dir.mkdir()

        ctx = build_ctx(project_dir=different_dir)
        ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                _rk = "autoskillit.server._lifespan.register_active_kitchen"
                with patch(_rk) as mock_register_kitchen:
                    await _skill_auto_gate_boot(ctx)

        mock_register_kitchen.assert_called_once_with(
            ctx.kitchen_id, os.getpid(), str(different_dir)
        )

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_emits_structured_log(self, tool_ctx, monkeypatch):
        """_skill_auto_gate_boot emits info log with event name, gate_state, and kitchen_id."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    with structlog.testing.capture_logs() as logs:
                        await _skill_auto_gate_boot(tool_ctx)

        assert any(
            entry.get("event") == "skill_auto_gate_boot"
            and entry.get("gate_state") == "open"
            and entry.get("kitchen_id") is not None
            for entry in logs
        )

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_writes_hook_config(self, tool_ctx, monkeypatch):
        """SKILL+HEADLESS+AUTO_GATE: _write_hook_config is called once."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config"
        ) as mock_write_hook_config:
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        mock_write_hook_config.assert_called_once_with()
        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_skips_when_both_absent(self, tool_ctx, monkeypatch):
        """Neither HEADLESS nor AUTO_GATE: closed (notification-capable backend)."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.delenv(HEADLESS_AUTO_GATE_ENV_VAR, raising=False)

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = True
        tool_ctx.backend = mock_backend

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config"
        ) as mock_write_hook_config:
            _rk = "autoskillit.server._lifespan.register_active_kitchen"
            with patch(_rk) as mock_register_kitchen:
                await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False
        mock_write_hook_config.assert_not_called()
        mock_register_kitchen.assert_not_called()

    @pytest.mark.anyio
    async def test_codex_non_headless_pre_reveal_opens_gate(self, build_ctx, monkeypatch):
        """Non-notification backend non-headless session pre-reveals kitchen and opens gate."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import HEADLESS_ENV_VAR, MCP_CLIENT_BACKEND_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "codex")

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = False
        ctx = build_ctx(backend=mock_backend)
        ctx.gate = DefaultGateState(enabled=False)

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(ctx)

        assert ctx.gate.enabled is True, "Gate should be open for non-notification backend"
        assert ctx.kitchen_id is not None, "kitchen_id should be set by pre-reveal path"
        assert ctx.active_recipe_packs == frozenset()
        assert ctx.active_recipe_steps == {}

    @pytest.mark.anyio
    async def test_codex_non_headless_pre_reveal_suppresses_disabled_subset(
        self, build_ctx, monkeypatch
    ):
        """Non-notification backend: disabled config subsets are hidden even though kitchen
        was pre-revealed at startup."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import HEADLESS_ENV_VAR, MCP_CLIENT_BACKEND_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import mcp
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "codex")

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = False
        # Disable the "plan-review" subset (simulates a user-configured disabled subset)
        ctx = build_ctx(backend=mock_backend)
        ctx.gate = DefaultGateState(enabled=False)
        ctx.config.subsets.disabled = ["plan-review"]

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(ctx)

        # plan-review resources should remain hidden
        templates = await mcp.list_resource_templates()
        uris = {t.uri_template for t in templates}
        assert "agent://plan-review/{name}" not in uris, (
            "plan-review resources must remain hidden when plan-review subset is disabled"
        )


@pytest.mark.feature("skill")
class TestSkillAutoGateBootRegistry:
    """SKILL session type registry wiring."""

    def test_lifespan_boot_registry_maps_skill_to_handler(self):
        """SessionType.SKILL maps to _skill_auto_gate_boot in _LIFESPAN_BOOT_REGISTRY."""
        from autoskillit.core import SessionType
        from autoskillit.server._lifespan import (
            _LIFESPAN_BOOT_REGISTRY,
            _skill_auto_gate_boot,
        )

        assert _LIFESPAN_BOOT_REGISTRY[SessionType.SKILL] is _skill_auto_gate_boot

    def test_lifespan_boot_registry_covers_all_session_types(self):
        """_LIFESPAN_BOOT_REGISTRY has no None values (all session types wired)."""
        from autoskillit.core import SessionType
        from autoskillit.server._lifespan import _LIFESPAN_BOOT_REGISTRY

        for session_type in SessionType:
            assert _LIFESPAN_BOOT_REGISTRY.get(session_type) is not None, (
                f"{session_type} has no boot handler in _LIFESPAN_BOOT_REGISTRY"
            )

    def test_headless_auto_gate_env_var_constant_value(self) -> None:
        from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, HEADLESS_AUTO_GATE_ENV_VAR

        assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"
        assert HEADLESS_AUTO_GATE_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS
