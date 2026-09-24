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

import autoskillit.server.lifecycle._state as lifecycle_state
import autoskillit.server.tools._preflight as preflight
import autoskillit.server.tools.tools_fleet_dispatch as tools_fleet_dispatch
from autoskillit.hook_registry import HookDef

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestFleetDispatchPreflightWiring:
    """Structural tests confirming preflight is wired into dispatch_food_truck."""

    def test_dispatch_food_truck_calls_preflight(self) -> None:
        """The dispatch path must call the helper that runs feasibility preflight."""
        from autoskillit.server.tools import tools_fleet_dispatch
        from autoskillit.server.tools.tools_fleet_dispatch._handlers import _prepare_dispatch

        dispatch_source = inspect.getsource(tools_fleet_dispatch.dispatch_food_truck)
        prepare_source = inspect.getsource(_prepare_dispatch)
        assert "_prepare_dispatch(" in dispatch_source
        assert "_check_dispatch_feasibility(" in prepare_source

    def test_preflight_called_before_execute_dispatch(self) -> None:
        """The helper that runs preflight must precede execute_dispatch."""
        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch.dispatch_food_truck)
        preflight_pos = source.find("_prepare_dispatch(")
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
        from autoskillit.core import (
            BackendCapabilities,
            FinalizedRecipeStep,
            RecipeFlowEdge,
        )
        from tests.server._helpers import _make_finalized_projection

        tool_ctx = build_ctx_open()

        caps = BackendCapabilities(
            applicable_guards=frozenset(),
            anthropic_provider_capable=False,
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = caps
        tool_ctx.backend = backend

        recipe_step = FinalizedRecipeStep(
            name="s1",
            tool="run_skill",
            provider="",
        )
        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": True,
            "post_prune_step_names": ["s1"],
            "_finalized_projection": _make_finalized_projection(
                steps=(recipe_step,),
                edges=(RecipeFlowEdge("s1", "success", "done", None, None),),
            ),
        }
        recipe_info = MagicMock()
        recipe_info.path = Path("/fake/recipe.yaml")
        tool_ctx.recipes.find.return_value = recipe_info

        recipe_obj = MagicMock()
        recipe_obj.steps = {"s1": recipe_step}
        tool_ctx.recipes.load.return_value = recipe_obj

        synthetic = HookDef(
            matcher=r"Read|Write|Edit",
            scripts=["guards/synthetic_test_hook.py"],
            codex_status="fix-required",
            mechanism="deny",
        )

        mock_execute = AsyncMock()
        with (
            patch.object(lifecycle_state, "_ctx", tool_ctx),
            patch.object(preflight, "HOOK_REGISTRY", [synthetic]),
            patch.object(
                tools_fleet_dispatch,
                "execute_dispatch",
                mock_execute,
            ),
            patch(
                "autoskillit.server.lifecycle._session_scope.admit_tool_session_scope",
                return_value=None,
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


class TestFleetDispatchPreflightLaunchEvidenceDeferral:
    def test_codex_pinned_join_required_step_defers_to_dispatch_evidence(
        self, build_ctx_open: Any, tmp_path: Path
    ) -> None:
        from autoskillit.config import AgentBackendConfig
        from autoskillit.core import FinalizedRecipeStep, JoinSpec, SkillSemanticPlan
        from autoskillit.execution.backends import get_backend
        from autoskillit.server.tools.tools_fleet_dispatch import _handlers
        from tests.server._helpers import _make_finalized_projection

        tool_ctx = build_ctx_open()
        tool_ctx.backend = get_backend("codex")
        tool_ctx.temp_dir = tmp_path
        tool_ctx.config.agent_backend = AgentBackendConfig(
            backend="codex",
            step_overrides={"s1": "codex"},
        )
        resolver = MagicMock()
        resolver.resolve_invocation.return_value = MagicMock(
            root=MagicMock(
                semantic_plan=SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
            )
        )
        tool_ctx.skill_resolver = resolver
        projection = _make_finalized_projection(
            steps=(FinalizedRecipeStep(name="s1", tool="run_skill", skill_name="join-root"),),
        )

        with (
            patch.object(
                _handlers,
                "_load_preflight_projection",
                return_value=(None, projection),
            ),
            patch.object(_handlers, "_prepare_managed_join", return_value=(None, None)),
        ):
            result = _handlers._prepare_dispatch(
                tool_ctx,
                "test-recipe",
                None,
                None,
                "food-truck",
                None,
                None,
            )

        assert not isinstance(result, str), result
