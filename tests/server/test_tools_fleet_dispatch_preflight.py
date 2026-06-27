"""Tests for dispatch_food_truck preflight integration.

Verifies that the fleet dispatch path references the shared
_check_dispatch_feasibility function and that the preflight
runs before execute_dispatch.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.hook_registry import HookDef

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestFleetDispatchPreflightWiring:
    """Structural tests confirming preflight is wired into dispatch_food_truck."""

    def test_dispatch_food_truck_calls_preflight(self) -> None:
        """dispatch_food_truck source must call _check_dispatch_feasibility."""
        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch.dispatch_food_truck)
        assert "_check_dispatch_feasibility" in source, (
            "dispatch_food_truck must call _check_dispatch_feasibility"
        )

    def test_preflight_called_before_execute_dispatch(self) -> None:
        """In the source order, _check_dispatch_feasibility must appear before
        the execute_dispatch call."""
        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch.dispatch_food_truck)
        preflight_pos = source.find("_check_dispatch_feasibility")
        execute_pos = source.find("execute_dispatch(")
        assert preflight_pos > 0
        assert execute_pos > 0
        assert preflight_pos < execute_pos, (
            f"Preflight must be called before execute_dispatch "
            f"(preflight at {preflight_pos}, execute at {execute_pos})"
        )


class TestFleetDispatchPreflightBehavioral:
    """Behavioral tests: preflight blocks execute_dispatch on incompatible backend."""

    @pytest.mark.anyio
    async def test_execute_dispatch_not_called_on_preflight_failure(
        self, build_ctx_open: Any
    ) -> None:
        """When preflight fails, execute_dispatch must not be called."""
        from autoskillit.core import BackendCapabilities

        tool_ctx = build_ctx_open()

        caps = BackendCapabilities(
            applicable_guards=frozenset(),
            anthropic_provider_capable=False,
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = caps
        tool_ctx.backend = backend

        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": True,
            "post_prune_step_names": ["s1"],
        }
        recipe_info = MagicMock()
        recipe_info.path = Path("/fake/recipe.yaml")
        tool_ctx.recipes.find.return_value = recipe_info

        recipe_obj = MagicMock()
        step_mock = MagicMock()
        step_mock.tool = "run_skill"
        step_mock.provider = ""
        recipe_obj.steps = {"s1": step_mock}
        tool_ctx.recipes.load.return_value = recipe_obj

        synthetic = HookDef(
            matcher=r"Read|Write|Edit",
            scripts=["guards/synthetic_test_hook.py"],
            codex_status="fix-required",
            mechanism="deny",
        )

        mock_execute = AsyncMock()
        with (
            patch("autoskillit.server._state._ctx", tool_ctx),
            patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch.execute_dispatch",
                mock_execute,
            ),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch._require_fleet",
                lambda _name: None,
            ),
        ):
            from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

            ctx_mock = AsyncMock()
            result = await dispatch_food_truck(
                recipe="test-recipe",
                task="test task",
                ctx=ctx_mock,
            )

        mock_execute.assert_not_called()
        parsed = json.loads(result)
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert parsed.get("success") is False

    @pytest.mark.anyio
    async def test_dispatch_food_truck_blocks_on_dispatch_infeasible(
        self, build_ctx_open: Any
    ) -> None:
        """When load_and_validate returns dispatch_feasible=False, dispatch_food_truck
        must NOT call execute_dispatch and must return a dispatch_infeasible response."""
        from autoskillit.core import BackendCapabilities

        tool_ctx = build_ctx_open()

        caps = BackendCapabilities(
            applicable_guards=frozenset(),
            anthropic_provider_capable=False,
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = caps
        tool_ctx.backend = backend

        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": True,
            "dispatch_feasible": False,
            "infeasible_steps": ["gate_backend_write"],
            "post_prune_step_names": ["gate_backend_write"],
        }
        recipe_info = MagicMock()
        recipe_info.path = Path("/fake/recipe.yaml")
        tool_ctx.recipes.find.return_value = recipe_info

        mock_execute = AsyncMock()
        with (
            patch("autoskillit.server._state._ctx", tool_ctx),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch.execute_dispatch",
                mock_execute,
            ),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch._require_fleet",
                lambda _name: None,
            ),
        ):
            from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

            ctx_mock = AsyncMock()
            result = await dispatch_food_truck(
                recipe="test-recipe",
                task="test task",
                ctx=ctx_mock,
            )

        mock_execute.assert_not_called()
        parsed = json.loads(result)
        assert parsed.get("success") is False
        assert "gate_backend_write" in parsed.get(
            "user_visible_message", ""
        ) or "dispatch_infeasible" in str(parsed)

    @pytest.mark.anyio
    async def test_dispatch_food_truck_injects_capability_overrides(
        self, build_ctx_open: Any
    ) -> None:
        """dispatch_food_truck must inject backend capability overrides into
        load_and_validate's ingredient_overrides parameter."""
        from autoskillit.core import BackendCapabilities

        tool_ctx = build_ctx_open()

        caps = BackendCapabilities(
            applicable_guards=frozenset(),
            anthropic_provider_capable=False,
            git_metadata_writable=False,
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = caps
        tool_ctx.backend = backend

        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": True,
            "dispatch_feasible": True,
            "post_prune_step_names": [],
        }
        recipe_info = MagicMock()
        recipe_info.path = Path("/fake/recipe.yaml")
        tool_ctx.recipes.find.return_value = recipe_info

        mock_execute = AsyncMock()
        with (
            patch("autoskillit.server._state._ctx", tool_ctx),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch.execute_dispatch",
                mock_execute,
            ),
            patch(
                "autoskillit.server.tools.tools_fleet_dispatch._require_fleet",
                lambda _name: None,
            ),
        ):
            from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

            ctx_mock = AsyncMock()
            await dispatch_food_truck(
                recipe="test-recipe",
                task="test task",
                ctx=ctx_mock,
            )

        call_kwargs = tool_ctx.recipes.load_and_validate.call_args.kwargs
        ingredient_overrides = call_kwargs.get("ingredient_overrides", {})
        assert ingredient_overrides.get("backend_supports_git_write") == "false", (
            "dispatch_food_truck must inject backend_supports_git_write='false' "
            f"for codex backend. Got: {ingredient_overrides}"
        )
