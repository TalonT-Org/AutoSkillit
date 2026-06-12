"""Tests for dispatch-feasibility preflight in open_kitchen and dispatch_food_truck.

These tests verify the temporal-ordering guarantee: the gate is closed
when a recipe's run_skill steps are infeasible for the current backend,
preventing irreversible side effects from side-effect tools between
open_kitchen and the first run_skill.

The preflight is implemented as `_check_dispatch_feasibility()` in
server/tools/tools_execution.py. These tests cover both the function
itself and its integration into open_kitchen and dispatch_food_truck.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
        name="codex",
        applicable_guards=frozenset(),
        anthropic_provider_capable=False,
    )
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = caps
    return backend


def _make_claude_backend() -> MagicMock:
    """Create a mock claude-code backend with applicable_guards."""
    from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE, BackendCapabilities

    caps = BackendCapabilities(
        name=AGENT_BACKEND_CLAUDE_CODE,
        applicable_guards=frozenset({"skill_load_guard"}),
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
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_cmd"),
            "step2": _make_recipe_step("step2", tool="run_python"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1", "step2"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is None, f"Expected None, got: {result}"

    def test_compatible_backend_passes(self) -> None:
        """Backend with applicable_guards covering the fix-required hook passes (1e)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_claude_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is None, f"Expected None for compatible backend, got: {result}"

    def test_incompatible_backend_fails(self) -> None:
        """Backend with empty applicable_guards returns error (1b)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert parsed.get("success") is False

    def test_empty_post_prune_names_returns_none(self) -> None:
        """An empty post-prune step list (all pruned) returns None (1d)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=[],
                active_recipe_steps={},
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is None

    def test_no_fix_required_hooks_returns_none(self) -> None:
        """A registry with no fix-required entries passes regardless of backend (1e)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", []):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is None

    def test_dormancy_synthetic_hook_always_blocks(self) -> None:
        """Synthetic fix-required hook permanently exercises failure path (1f)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        dormancy = _make_dormancy_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [dormancy]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("success") is False
        assert parsed.get("stage") == "dispatch_feasibility_preflight"
        assert "dormancy_test_hook" in str(parsed) or "fix-required" in str(parsed)

    def test_provider_override_excludes_step(self) -> None:
        """A run_skill step with a provider profile that sets ANTHROPIC_BASE_URL
        on a non-anthropic backend is excluded from the check (1h)."""
        from autoskillit.config._config_dataclasses import (
            ProviderProfileDef,
            ProvidersConfig,
        )
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill", provider="myprofile"),
        }
        profile = ProviderProfileDef(
            name="myprofile",
            base_url="https://example.com",
        )
        providers_config = ProvidersConfig(
            profiles={"myprofile": profile},
            resolved_profiles={"myprofile": profile},
            recipe_overrides={},
            step_overrides={},
            default_provider="myprofile",
        )
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=providers_config,
            )
        assert result is None, f"Provider override should exclude step from check, got: {result}"

    def test_no_backend_returns_none(self) -> None:
        """When backend is None, the preflight returns None (no check possible)."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = None
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is None

    def test_error_includes_escape_hatch(self) -> None:
        """Preflight error envelope mentions the provider-override escape hatch."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

        backend = _make_codex_backend()
        active_steps: dict[str, Any] = {
            "step1": _make_recipe_step("step1", tool="run_skill"),
        }
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.hook_registry.HOOK_REGISTRY", [synthetic]):
            result = _check_dispatch_feasibility(
                post_prune_step_names=["step1"],
                active_recipe_steps=active_steps,
                backend=backend,
                config_providers=MagicMock(),
            )
        assert result is not None
        parsed = json.loads(result)
        assert "escape_hatch" in parsed or "ANTHROPIC_BASE_URL" in str(parsed)


# ---------------------------------------------------------------------------
# Wiring tests: confirm preflight is called from open_kitchen and dispatch_food_truck
# ---------------------------------------------------------------------------


class TestPreflightWiring:
    """Structural tests confirming the preflight is wired into call sites."""

    def test_check_dispatch_feasibility_is_importable(self) -> None:
        """The shared function exists and is importable."""
        from autoskillit.server.tools.tools_execution import _check_dispatch_feasibility

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
