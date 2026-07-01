"""End-to-end chain tests for capability admission control.

Verifies the full chain from backend capability detection through
load_and_validate to the dispatch_feasible signal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from autoskillit.core import BACKEND_CAPABILITY_INGREDIENTS, CAPABILITY_GATE_CALLABLES
from autoskillit.recipe._api import load_and_validate

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_capability_ingredient_keys_match_registry() -> None:
    """BACKEND_CAPABILITY_INGREDIENTS must include backend_supports_git_write."""
    assert "backend_supports_git_write" in BACKEND_CAPABILITY_INGREDIENTS


def test_capability_gate_callables_includes_gate_backend_write() -> None:
    """CAPABILITY_GATE_CALLABLES must include gate_backend_write."""
    assert "gate_backend_write" in CAPABILITY_GATE_CALLABLES


def test_codex_backend_reachable_gate_returns_infeasible() -> None:
    """Codex + implementation recipe chain: backend_supports_git_write=false
    produces dispatch_feasible=False because the reachable gate (post-prune
    route-repair redirects create_impl_worktree.on_success to gate_backend_write)
    blocks dispatch via admission control."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
    assert result.get("valid") is True
    assert result.get("dispatch_feasible") is False
    assert "gate_backend_write" in result.get("infeasible_steps", [])


def test_claude_code_backend_produces_feasible_recipe() -> None:
    """Claude Code + implementation recipe: backend_supports_git_write=true
    produces dispatch_feasible=True."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "true"},
        backend_name="claude-code",
    )
    assert result.get("valid") is True
    assert result.get("dispatch_feasible") is True
    assert "infeasible_steps" not in result


def test_capability_override_parity() -> None:
    """Server and fleet capability override functions must produce identical output.

    _backend_capability_overrides (IL-3 server) and _build_capability_overrides
    (IL-2 fleet) are parallel implementations — IL-2 cannot import IL-3. This
    test asserts value-level parity for every capability state.
    """
    from unittest.mock import MagicMock

    from autoskillit.fleet._api import _build_capability_overrides
    from autoskillit.server.tools._auto_overrides import (
        _backend_capability_overrides,
        _provider_aware_capability_overrides,
    )

    for writable in (True, False):
        backend = MagicMock()
        backend.capabilities.git_metadata_writable = writable
        assert _backend_capability_overrides(backend) == _build_capability_overrides(backend), (
            f"Parity violation for git_metadata_writable={writable}"
        )

    assert _backend_capability_overrides(None) == _build_capability_overrides(None), (
        "Parity violation for backend=None"
    )

    # Graceful degradation: _provider_aware_capability_overrides with no provider/steps
    # input must equal _backend_capability_overrides for all backend states.
    for writable in (True, False):
        backend = MagicMock()
        backend.capabilities.git_metadata_writable = writable
        backend.capabilities.anthropic_provider_capable = True
        degraded, _ = _provider_aware_capability_overrides(backend, "", None, None)
        assert degraded == _backend_capability_overrides(backend), (
            f"Graceful-degradation violation for git_metadata_writable={writable}"
        )

    degraded_none, _ = _provider_aware_capability_overrides(None, "", None, None)
    assert degraded_none == _backend_capability_overrides(None), (
        "Graceful-degradation violation for backend=None"
    )


@pytest.mark.anyio
async def test_open_kitchen_refuses_doa_codex_pipeline() -> None:
    """open_kitchen must return success=False with kitchen='dispatch_infeasible'
    when load_and_validate reports dispatch_feasible=False."""
    import json
    from unittest.mock import AsyncMock, patch

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.gate_infrastructure_ready = True
    tool_ctx.recipe_name = "implementation"
    tool_ctx.kitchen_id = "test-kitchen"
    tool_ctx.backend.name = "codex"
    tool_ctx.recipes.load_and_validate.return_value = {
        "content": "name: implementation\nsteps:\n  build:\n    cmd: task build\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
        "suggestions": [],
        "post_prune_step_names": ["build"],
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
    }
    tool_ctx.recipes.find.return_value = None

    fastmcp_ctx = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=tool_ctx):
        result = await open_kitchen(name="implementation", ctx=fastmcp_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "dispatch_infeasible"
    assert "gate_backend_write" in parsed["infeasible_steps"]
    tool_ctx.gate.disable.assert_called_once()
    tool_ctx.gate.enable.assert_not_called()
    fastmcp_ctx.disable_components.assert_called_once_with(tags={"kitchen"})


def _make_codex_backend() -> MagicMock:
    """Return a MagicMock backend resembling Codex (non-git-writable, non-anthropic-capable)."""
    from types import SimpleNamespace

    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )
    return backend


def _make_claude_backend() -> MagicMock:
    """Return a MagicMock backend resembling Claude Code (git-writable, anthropic-capable)."""
    from types import SimpleNamespace

    backend = MagicMock()
    backend.name = "claude-code"
    backend.capabilities = SimpleNamespace(
        git_metadata_writable=True,
        anthropic_provider_capable=True,
    )
    return backend


def _make_recipe_step(name: str, provider: str = "") -> MagicMock:
    """Return a MagicMock recipe step with skip_when_false and optional provider."""
    step = MagicMock()
    step.name = name
    step.tool = "run_skill"
    step.skip_when_false = "inputs.backend_supports_git_write"
    step.provider = provider
    return step


def _make_feasible_load_result(
    post_prune_step_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return a load_and_validate result dict for a feasible dispatch."""
    return {
        "content": "name: implementation\nsteps:\n  implement:\n    tool: run_skill\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
        "suggestions": [],
        "post_prune_step_names": post_prune_step_names or ["implement", "retry_worktree"],
        "dispatch_feasible": True,
    }


def _setup_provider_override_ctx(tool_ctx: MagicMock) -> MagicMock:
    """Configure tool_ctx as a Codex backend with provider-overridden guarded steps."""
    from types import SimpleNamespace

    tool_ctx.backend = MagicMock()
    tool_ctx.backend.name = "codex"
    tool_ctx.backend.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )

    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "retry_worktree": _make_recipe_step("retry_worktree", provider="minimax"),
    }
    tool_ctx.recipes.load.return_value = recipe_obj

    return tool_ctx


def test_provider_aware_capability_override_all_overridden_returns_true() -> None:
    """All guarded run_skill steps have ANTHROPIC_BASE_URL provider -> returns 'true'."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"minimax": {}},
        step_overrides={
            "implement": "minimax",
            "retry_worktree": "minimax",
            "fix": "minimax",
        },
    )
    steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "retry_worktree": _make_recipe_step("retry_worktree", provider="minimax"),
        "fix": _make_recipe_step("fix", provider="minimax"),
    }

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
    ):
        result, _ = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "true"}


def test_provider_aware_capability_override_partial_overrides_stays_false() -> None:
    """Partial provider overrides (conservative): capability stays 'false'."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"minimax": {}},
        step_overrides={
            "implement": "minimax",
        },
    )
    steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        side_effect=_per_step_resolve,
    ):
        result, _ = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "false"}


def test_provider_aware_capability_override_no_overrides_preserves_false() -> None:
    """Codex backend with no provider overrides → backend_supports_git_write stays 'false'."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig()
    steps = {
        "implement": _make_recipe_step("implement", provider=""),
    }

    result, _ = _provider_aware_capability_overrides(
        backend,
        "implementation",
        providers,
        steps,  # type: ignore[arg-type]
    )
    assert result == {"backend_supports_git_write": "false"}


def test_provider_aware_capability_override_claude_backend_no_op() -> None:
    """Claude Code backend (anthropic_provider_capable=True) — no-op path returns 'true'."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_claude_backend()
    providers = ProvidersConfig()
    steps = {
        "implement": _make_recipe_step("implement", provider=""),
    }

    result, _ = _provider_aware_capability_overrides(
        backend,
        "implementation",
        providers,
        steps,  # type: ignore[arg-type]
    )
    assert result == {"backend_supports_git_write": "true"}


@pytest.mark.anyio
async def test_open_kitchen_codex_with_provider_overrides_feasible() -> None:
    """open_kitchen must return success=True with kitchen='open' when Codex
    backend has provider-overridden guarded steps (provider-aware path)."""
    import json
    from unittest.mock import AsyncMock, patch

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.gate_infrastructure_ready = True
    tool_ctx.recipe_name = "implementation"
    tool_ctx.kitchen_id = "test-kitchen"
    _setup_provider_override_ctx(tool_ctx)
    tool_ctx.recipes.load_and_validate.return_value = _make_feasible_load_result()

    fastmcp_ctx = AsyncMock()

    with (
        patch("autoskillit.server._get_ctx", return_value=tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
        ),
    ):
        result = await open_kitchen(name="implementation", ctx=fastmcp_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "implement" in parsed.get("post_prune_step_names", [])


@pytest.mark.anyio
async def test_load_recipe_codex_with_provider_overrides_no_infeasible() -> None:
    """load_recipe must NOT return dispatch_infeasible when Codex backend has
    provider-overridden guarded steps."""
    import json
    from unittest.mock import patch

    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.kitchen_id = "test-kitchen"
    _setup_provider_override_ctx(tool_ctx)
    tool_ctx.recipes.load_and_validate.return_value = _make_feasible_load_result()

    with (
        patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=tool_ctx,
        ),
        patch(
            "autoskillit.server.tools.tools_recipe._require_enabled",
            return_value=None,
        ),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
        ),
    ):
        result = await load_recipe(name="implementation")

    parsed = json.loads(result)
    assert "dispatch_infeasible" not in parsed
    assert parsed.get("success") is not False


def test_get_recipe_codex_with_provider_overrides_no_infeasible() -> None:
    """get_recipe resource must return raw YAML content (not dispatch_infeasible)
    when Codex backend has provider-overridden guarded steps."""
    from unittest.mock import patch

    from autoskillit.server.tools.tools_kitchen import get_recipe

    tool_ctx = MagicMock()
    _setup_provider_override_ctx(tool_ctx)
    tool_ctx.recipes.load_and_validate.return_value = {
        **_make_feasible_load_result(),
        "content": "name: implementation\nsteps:\n  implement:\n    tool: run_skill\n",
    }

    with (
        patch(
            "autoskillit.server._state._get_ctx_or_none",
            return_value=tool_ctx,
        ),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
        ),
    ):
        result = get_recipe("implementation")

    assert isinstance(result, str)
    assert "error" not in result.lower()
    assert '"dispatch_feasible": false' not in result


@pytest.mark.anyio
async def test_validate_recipe_codex_with_provider_overrides_no_infeasible() -> None:
    """validate_recipe must return valid=True and NOT contain dispatch_infeasible
    when Codex backend has provider-overridden guarded steps."""
    import json
    from unittest.mock import patch

    from autoskillit.server.tools.tools_recipe import validate_recipe
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    _setup_provider_override_ctx(tool_ctx)
    tool_ctx.recipes.load.return_value.name = "implementation"
    tool_ctx.recipes.load.return_value.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
    }
    tool_ctx.recipes.validate_from_path.return_value = {
        "valid": True,
        "errors": [],
        "suggestions": [],
    }

    with (
        patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=tool_ctx,
        ),
        patch(
            "autoskillit.server.tools.tools_recipe._require_enabled",
            return_value=None,
        ),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
        ),
    ):
        result = await validate_recipe(script_path="/fake/recipe.yaml")

    parsed = json.loads(result)
    assert "dispatch_infeasible" not in parsed
    assert parsed.get("valid") is True


@pytest.mark.anyio
async def test_dispatch_food_truck_codex_with_provider_overrides_not_rejected(
    build_ctx_open: Any,
) -> None:
    """dispatch_food_truck must NOT be rejected at preflight when Codex backend
    has provider-overridden guarded steps. Additionally, provider_capability_overrides
    must be forwarded to execute_dispatch."""
    import json
    from unittest.mock import AsyncMock, patch

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
    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": True,
        "dispatch_feasible": True,
        "post_prune_step_names": ["implement"],
    }

    mock_outcome = MagicMock()
    mock_outcome.to_envelope.return_value = json.dumps({"success": True})
    mock_outcome.dispatch_id = None
    mock_dispatch_result = MagicMock()
    mock_dispatch_result.outcome = mock_outcome
    mock_execute = AsyncMock(return_value=mock_dispatch_result)

    with (
        patch("autoskillit.server._state._ctx", tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
        ),
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

        result = await dispatch_food_truck(
            recipe="implementation",
            task="test task",
            ctx=AsyncMock(),
        )

    mock_execute.assert_called_once()
    assert mock_execute.call_args.kwargs["provider_capability_overrides"] == {
        "backend_supports_git_write": "true"
    }
    parsed = json.loads(result)
    assert "FLEET_RECIPE_INVALID" not in parsed.get("error", "")
    assert parsed.get("success") is True


def test_provider_aware_override_returns_resolution_detail() -> None:
    """Test 1A: _provider_aware_capability_overrides returns a tuple
    (dict, CapabilityResolutionDetail | None). Partial-override scenario
    produces detail with bail_step populated and resolution_path='partial_bail'.
    """
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core import CapabilityResolutionDetail
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"minimax": {}},
        step_overrides={"implement": "minimax"},
    )
    steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    from unittest.mock import patch

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        side_effect=_per_step_resolve,
    ):
        overrides, detail = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "partial_bail"
    assert detail.bail_step is not None
    assert detail.bail_step == "fix"
    assert len(detail.resolved_steps) >= 1
    assert overrides == {"backend_supports_git_write": "false"}


@pytest.mark.anyio
async def test_open_kitchen_partial_override_infeasible_includes_provider_guidance() -> None:
    """Test 1B: open_kitchen with Codex + partial provider overrides produces
    dispatch_infeasible envelope with missing_provider_steps and escape_hatch."""
    import json
    from unittest.mock import AsyncMock, patch

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.gate_infrastructure_ready = True
    tool_ctx.recipe_name = "implementation"
    tool_ctx.kitchen_id = "test-kitchen"
    tool_ctx.backend.name = "codex"

    from types import SimpleNamespace

    tool_ctx.backend = MagicMock()
    tool_ctx.backend.name = "codex"
    tool_ctx.backend.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )

    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj

    tool_ctx.recipes.load_and_validate.return_value = {
        **_make_feasible_load_result(),
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
    }

    fastmcp_ctx = AsyncMock()

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    with (
        patch("autoskillit.server._get_ctx", return_value=tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            side_effect=_per_step_resolve,
        ),
    ):
        result = await open_kitchen(name="implementation", ctx=fastmcp_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "dispatch_infeasible"
    assert "missing_provider_steps" in parsed
    assert "escape_hatch" in parsed
    assert "ANTHROPIC_BASE_URL" in parsed["escape_hatch"]


def test_wildcard_override_flips_capability_true() -> None:
    """Test 1C: wildcard ('*') provider override in step_overrides correctly
    resolves per-step with ANTHROPIC_BASE_URL, returning 'true'."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"minimax": {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}},
        step_overrides={"*": "minimax"},
    )
    steps = {
        "implement": _make_recipe_step("implement", provider=""),
        "fix": _make_recipe_step("fix", provider=""),
    }

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        return_value=("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
    ):
        result, detail = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "true"}
    assert detail is not None
    assert detail.resolution_path == "all_pass"
    assert detail.bail_step is None


def test_non_base_url_provider_extras_bail() -> None:
    """Test 1D: provider profile with extras but no ANTHROPIC_BASE_URL
    (e.g. Bedrock-style {AWS_REGION: us-east-1}) correctly bails."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"bedrock": {"AWS_REGION": "us-east-1"}},
        step_overrides={"implement": "bedrock"},
    )
    steps = {
        "implement": _make_recipe_step("implement", provider="bedrock"),
        "fix": _make_recipe_step("fix", provider=""),
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "bedrock":
            return ("bedrock", {"AWS_REGION": "us-east-1"})
        return ("", None)

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        side_effect=_per_step_resolve,
    ):
        result, detail = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "false"}
    assert detail is not None
    assert detail.resolution_path == "partial_bail"
    assert detail.bail_step is not None


@pytest.mark.anyio
async def test_load_recipe_partial_override_infeasible_includes_provider_guidance() -> None:
    """Test 1G: load_recipe with Codex + partial provider overrides returns
    dispatch_infeasible response with missing_provider_steps and escape_hatch."""
    import json
    from unittest.mock import patch

    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.kitchen_id = "test-kitchen"

    from types import SimpleNamespace

    tool_ctx.backend = MagicMock()
    tool_ctx.backend.name = "codex"
    tool_ctx.backend.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )

    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj

    tool_ctx.recipes.load_and_validate.return_value = {
        **_make_feasible_load_result(),
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    with (
        patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=tool_ctx,
        ),
        patch(
            "autoskillit.server.tools.tools_recipe._require_enabled",
            return_value=None,
        ),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            side_effect=_per_step_resolve,
        ),
    ):
        result = await load_recipe(name="implementation")

    parsed = json.loads(result)
    assert parsed.get("dispatch_infeasible") is True
    assert "missing_provider_steps" in parsed
    assert "escape_hatch" in parsed


@pytest.mark.anyio
async def test_dispatch_food_truck_partial_override_infeasible_includes_guidance(
    build_ctx_open: Any,
) -> None:
    """Test 1H: dispatch_food_truck with Codex + partial provider overrides
    returns fleet error containing provider guidance."""
    import json
    from unittest.mock import AsyncMock, patch

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
    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": True,
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
        "post_prune_step_names": ["implement"],
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    with (
        patch("autoskillit.server._state._ctx", tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            side_effect=_per_step_resolve,
        ),
        patch(
            "autoskillit.server.tools.tools_fleet_dispatch._require_fleet",
            lambda _name: None,
        ),
    ):
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = await dispatch_food_truck(
            recipe="implementation",
            task="test task",
            ctx=AsyncMock(),
        )

    parsed = json.loads(result)
    assert "fleet_recipe_invalid" in parsed.get("error", "")
    assert "ANTHROPIC_BASE_URL" in parsed.get("user_visible_message", "")
