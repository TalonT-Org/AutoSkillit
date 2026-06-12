"""Tests for dispatch-feasibility preflight in open_kitchen and dispatch_food_truck.

These tests verify the temporal-ordering guarantee: the gate is closed
when a recipe's run_skill steps are infeasible for the current backend,
preventing irreversible side effects from side-effect tools between
open_kitchen and the first run_skill.

The preflight is implemented as `_check_dispatch_feasibility()` in
server/tools/_preflight.py. These tests cover both the function
itself and its integration into open_kitchen and dispatch_food_truck.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.config._config_dataclasses import ProvidersConfig

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_DEFAULT_PROVIDERS = ProvidersConfig()


def _make_recipe_step(name: str, tool: str | None = "run_skill", **kwargs: Any) -> MagicMock:
    """Create a mock RecipeStep with the given tool and other attributes."""
    step = MagicMock()
    step.name = name
    step.tool = tool
    for k, v in kwargs.items():
        setattr(step, k, v)
    return step


def _make_fix_required_hook() -> Any:
    """Create a synthetic HookDef with codex_status=fix-required."""
    from autoskillit.hook_registry import HookDef

    return HookDef(
        matcher=r"Read|Write|Edit",
        scripts=["guards/synthetic_test_hook.py"],
        codex_status="fix-required",
        mechanism="deny",
    )


def _make_dormancy_hook() -> Any:
    """Create a permanent dormancy-test HookDef."""
    from autoskillit.hook_registry import HookDef

    return HookDef(
        matcher=r"Read|Write|Edit|Bash",
        scripts=["guards/dormancy_test_hook.py"],
        codex_status="fix-required",
        mechanism="deny",
    )


def _make_codex_backend() -> MagicMock:
    """Create a mock codex backend with empty applicable_guards."""
    from autoskillit.core import BackendCapabilities

    caps = BackendCapabilities(
        applicable_guards=frozenset(),
        anthropic_provider_capable=False,
    )
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = caps
    return backend


def _make_claude_backend() -> MagicMock:
    """Create a mock claude-code backend with applicable_guards covering the synthetic hook."""
    from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE, BackendCapabilities

    caps = BackendCapabilities(
        applicable_guards=frozenset({"skill_load_guard", "synthetic_test_hook"}),
        anthropic_provider_capable=True,
    )
    backend = MagicMock()
    backend.name = AGENT_BACKEND_CLAUDE_CODE
    backend.capabilities = caps
    return backend


# ---------------------------------------------------------------------------
# Direct tests for _check_dispatch_feasibility (1b-1f, 1h)
# ---------------------------------------------------------------------------


class TestCheckDispatchFeasibilityUnit:
    """Unit tests for the shared _check_dispatch_feasibility function."""

    def test_no_run_skill_steps_returns_none(self) -> None:
        """A recipe with no run_skill steps passes the preflight (1c)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_cmd"),
            "step2": _make_recipe_step("step2", tool="run_python"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1", "step2"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is None, f"Expected None, got: {result}"

    def test_compatible_backend_passes(self) -> None:
        """Backend with applicable_guards covering the fix-required hook passes (1e)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_claude_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is None, f"Expected None for compatible backend, got: {result}"

    def test_incompatible_backend_fails(self) -> None:
        """Backend with empty applicable_guards returns error (1b)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert parsed.get("success") is False

    def test_empty_post_prune_names_returns_none(self) -> None:
        """An empty post-prune step list (all pruned) returns None (1d)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=[],
                active_recipe_steps={},
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is None

    def test_no_fix_required_hooks_returns_none(self) -> None:
        """A registry with no fix-required entries passes regardless of backend (1e)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", []):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is None

    def test_dormancy_synthetic_hook_always_blocks(self) -> None:
        """Synthetic fix-required hook permanently exercises failure path (1f)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        dormancy = _make_dormancy_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [dormancy]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("success") is False
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert "dormancy_test_hook" in str(parsed)

    def test_provider_override_excludes_step(self) -> None:
        """A run_skill step with a provider profile that sets ANTHROPIC_BASE_URL
        on a non-anthropic backend is excluded from the check (1h)."""
        from autoskillit.config._config_dataclasses import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill", provider="myprofile"),
        }
        providers_config = ProvidersConfig(
            profiles={"myprofile": {"base_url": "https://example.com"}},
            recipe_overrides={},
            step_overrides={},
            default_provider="myprofile",
        )
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=providers_config,
            )
        assert result is None, f"Provider override should exclude step from check, got: {result}"

    def test_no_backend_returns_none(self) -> None:
        """When backend is None, the preflight returns None (no check possible)."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = None
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is None

    def test_error_includes_escape_hatch(self) -> None:
        """Preflight error envelope mentions the provider-override escape hatch."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=_DEFAULT_PROVIDERS,
            )
        assert result is not None
        parsed = json.loads(result)
        assert "escape_hatch" in parsed or "ANTHROPIC_BASE_URL" in str(parsed)

    def test_real_hook_registry_codex_backend(self) -> None:
        """Exercise _check_dispatch_feasibility with the real production HOOK_REGISTRY."""
        from autoskillit.server.tools._preflight import (
            _check_dispatch_feasibility,
            _get_fix_required_hook_matchers,
        )

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }

        result = _check_dispatch_feasibility(
            post_prune_step_names=["step1"],
            active_recipe_steps=active_steps,
            backend=backend,
            config_providers=_DEFAULT_PROVIDERS,
        )

        has_unenforced = bool(
            _get_fix_required_hook_matchers(backend.capabilities.applicable_guards)
        )
        if has_unenforced:
            assert result is not None
            parsed = json.loads(result)
            assert parsed.get("stage") == "dispatch_feasibility_preflight"
        else:
            assert result is None


# ---------------------------------------------------------------------------
# Wiring tests: confirm preflight is called from open_kitchen and dispatch_food_truck
# ---------------------------------------------------------------------------


class TestPreflightWiring:
    """Structural tests confirming the preflight is wired into call sites."""

    def test_check_dispatch_feasibility_is_importable(self) -> None:
        """The shared function exists and is importable."""
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        assert callable(_check_dispatch_feasibility)

    def test_open_kitchen_module_imports_preflight(self) -> None:
        """tools_kitchen module references _check_dispatch_feasibility."""
        # The module should have a reference to the preflight function
        import inspect

        from autoskillit.server.tools import tools_kitchen

        source = inspect.getsource(tools_kitchen)
        assert "_check_dispatch_feasibility" in source, (
            "tools_kitchen.py must reference _check_dispatch_feasibility"
        )

    def test_fleet_dispatch_module_imports_preflight(self) -> None:
        """tools_fleet_dispatch module references _check_dispatch_feasibility."""
        import inspect

        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch)
        assert "_check_dispatch_feasibility" in source, (
            "tools_fleet_dispatch.py must reference _check_dispatch_feasibility"
        )


# ---------------------------------------------------------------------------
# Gate-closure tests: open_kitchen disables gate on preflight/validation failure
# ---------------------------------------------------------------------------


class TestPreflightGateClosure:
    """Tests verifying gate closure on preflight and validation failure paths."""

    @pytest.mark.anyio
    async def test_gate_closed_after_validation_failure(self, build_ctx_open: Any) -> None:
        """open_kitchen disables the gate when load_and_validate returns valid=False."""
        from pathlib import Path
        from unittest.mock import AsyncMock

        tool_ctx = build_ctx_open()
        assert tool_ctx.gate.enabled is True

        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": False,
            "errors": ["synthetic failure"],
            "content": "",
        }
        tool_ctx.recipes.find.return_value = MagicMock(path=Path("/fake/recipe.yaml"))

        async def _triage_passthrough(result, *_a, **_kw):
            return result

        with (
            patch("autoskillit.server._state._ctx", tool_ctx),
            patch(
                "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                side_effect=_triage_passthrough,
            ),
        ):
            from autoskillit.server.tools.tools_kitchen import open_kitchen

            ctx_mock = AsyncMock()
            result = await open_kitchen(name="test-recipe", ctx=ctx_mock)

        assert tool_ctx.gate.enabled is False
        parsed = json.loads(result)
        assert parsed.get("stage") == "recipe_validation"

    @pytest.mark.anyio
    async def test_open_kitchen_preflight_blocks_and_closes_gate(
        self, build_ctx_open: Any
    ) -> None:
        """open_kitchen returns preflight error and closes gate for incompatible backend."""
        from pathlib import Path
        from unittest.mock import AsyncMock

        tool_ctx = build_ctx_open()
        assert tool_ctx.gate.enabled is True

        tool_ctx.backend = _make_codex_backend()
        tool_ctx.recipes = MagicMock()
        tool_ctx.recipes.load_and_validate.return_value = {
            "valid": True,
            "content": "steps:\n  s1:\n    tool: run_skill",
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
        recipe_obj.ingredients = {}
        tool_ctx.recipes.load.return_value = recipe_obj

        async def _triage_passthrough(result, *_a, **_kw):
            return result

        synthetic = _make_fix_required_hook()
        with (
            patch("autoskillit.server._state._ctx", tool_ctx),
            patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]),
            patch(
                "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                side_effect=_triage_passthrough,
            ),
        ):
            from autoskillit.server.tools.tools_kitchen import open_kitchen

            ctx_mock = AsyncMock()
            result = await open_kitchen(name="test-recipe", ctx=ctx_mock)

        assert tool_ctx.gate.enabled is False
        parsed = json.loads(result)
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert parsed.get("success") is False
