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
    blocks dispatch via admission control.

    Note: valid may be False due to backend-incompatible-skill findings for
    merge-conflict steps (guarded by open_pr, not backend_supports_git_write).
    """
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
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
async def test_open_kitchen_refuses_doa_codex_pipeline(tmp_path: Path) -> None:
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
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    tool_ctx.temp_dir = tmp_path
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
    from autoskillit.core import CapabilityResolutionDetail
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
        result, detail = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "true"}
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "any_pass"
    assert detail.bail_step is None


def test_provider_aware_capability_override_partial_overrides_flips_true() -> None:
    """Partial provider overrides (any-suffices): at least one step with ANTHROPIC_BASE_URL
    flips capability to 'true'."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core import CapabilityResolutionDetail
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
        result, detail = _provider_aware_capability_overrides(
            backend,
            "implementation",
            providers,
            steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "true"}
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "any_pass"


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
async def test_open_kitchen_codex_with_provider_overrides_feasible(tmp_path: Path) -> None:
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
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    tool_ctx.temp_dir = tmp_path
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
    assert "implement" in parsed.get("step_index", {})


@pytest.mark.anyio
async def test_load_recipe_codex_with_provider_overrides_no_infeasible(
    tmp_path: Path,
) -> None:
    """load_recipe must NOT return dispatch_infeasible when Codex backend has
    provider-overridden guarded steps."""
    import json
    from unittest.mock import patch

    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server.conftest import _make_mock_ctx

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.kitchen_id = "test-kitchen"
    # New envelope: load_recipe persists the full recipe payload to
    # tool_ctx.temp_dir/responses/load_recipe/. Provide a real temp_dir
    # so artifact_dir.mkdir succeeds under the mock.
    tool_ctx.temp_dir = tmp_path
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
    (dict, CapabilityResolutionDetail | None). Any-suffices scenario with at
    least one step having ANTHROPIC_BASE_URL produces resolution_path='any_pass'.
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
    assert detail.resolution_path == "any_pass"
    assert detail.bail_step is None
    assert len(detail.resolved_steps) >= 1
    assert overrides == {"backend_supports_git_write": "true"}


@pytest.mark.anyio
async def test_open_kitchen_no_override_infeasible_includes_provider_guidance(
    tmp_path: Path,
) -> None:
    """Test 1B: open_kitchen with Codex + NO provider overrides (none_pass) produces
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
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    tool_ctx.temp_dir = tmp_path
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
        "implement": _make_recipe_step("implement", provider=""),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj

    tool_ctx.recipes.load_and_validate.return_value = {
        **_make_feasible_load_result(),
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
    }

    fastmcp_ctx = AsyncMock()

    with (
        patch("autoskillit.server._get_ctx", return_value=tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("", None),
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
    assert detail.resolution_path == "any_pass"
    assert detail.bail_step is None


def test_non_base_url_provider_extras_bail() -> None:
    """Test 1D: provider profile with extras but no ANTHROPIC_BASE_URL
    (e.g. Bedrock-style {AWS_REGION: us-east-1}) correctly bails with none_pass."""
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
    assert detail.resolution_path == "none_pass"
    assert detail.bail_step is None


def test_partial_override_real_recipe_nine_steps() -> None:
    """Test 1E: full 9-step guarded set, single step overridden, any-suffices flips to true."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core import CapabilityResolutionDetail
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    recipe_obj = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    _GIT_WRITE_STEPS = frozenset(
        name
        for name, step in recipe_obj.steps.items()
        if step.skip_when_false == "inputs.backend_supports_git_write" and step.tool == "run_skill"
    )
    backend = _make_codex_backend()
    providers = ProvidersConfig(
        profiles={"minimax": {}},
        step_overrides={"implement": "minimax"},
    )
    steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
        "merge_gate_fix": _make_recipe_step("merge_gate_fix", provider=""),
        "retry_worktree": _make_recipe_step("retry_worktree", provider=""),
        "rebase_conflict_fix": _make_recipe_step("rebase_conflict_fix", provider=""),
        "resolve_review": _make_recipe_step("resolve_review", provider=""),
        "resolve_pre_review_conflicts": _make_recipe_step(
            "resolve_pre_review_conflicts", provider=""
        ),
        "resolve_pre_resolve_conflicts": _make_recipe_step(
            "resolve_pre_resolve_conflicts", provider=""
        ),
        "resolve_ci": _make_recipe_step("resolve_ci", provider=""),
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
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
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "any_pass"
    assert detail.bail_step is None
    assert result == {"backend_supports_git_write": "true"}


@pytest.mark.anyio
async def test_all_infeasibility_paths_have_escape_hatch(build_ctx_open: Any) -> None:
    """Test 1F: structural parity — all three infeasibility paths plus preflight
    include ``escape_hatch`` and ``missing_provider_steps`` in their JSON response.
    """
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from autoskillit.core import BackendCapabilities, CapabilityResolutionDetail
    from autoskillit.server.tools.tools_kitchen import _dispatch_infeasible_response
    from tests.server.conftest import _make_mock_ctx

    detail = CapabilityResolutionDetail(
        resolved_steps=(("implement", "minimax", True), ("fix", "", False)),
        bail_step=None,
        resolution_path="none_pass",
    )

    # Path 1: _dispatch_infeasible_response (kitchen)
    result_dict = {
        "infeasible_steps": ["gate_backend_write"],
        "ingredients_table": None,
    }
    gate = MagicMock()
    ctx = AsyncMock()
    parsed = json.loads(
        await _dispatch_infeasible_response(
            result_dict, _make_codex_backend(), gate, ctx, capability_detail=detail
        )
    )
    assert "escape_hatch" in parsed
    assert "missing_provider_steps" in parsed

    # Path 2: load_recipe (recipe)
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

    def _per_step_resolve_recipe(_step_name, _recipe_name, _config_providers, step_provider=""):
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
            side_effect=_per_step_resolve_recipe,
        ),
    ):
        from autoskillit.server.tools.tools_recipe import load_recipe

        recipe_result = await load_recipe(name="implementation")
    parsed = json.loads(recipe_result)
    assert "escape_hatch" in parsed
    assert "missing_provider_steps" in parsed

    # Path 3: dispatch_food_truck (fleet dispatch)
    ft_ctx = build_ctx_open()
    caps = BackendCapabilities(
        applicable_guards=frozenset(),
        anthropic_provider_capable=False,
        git_metadata_writable=False,
    )
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = caps
    ft_ctx.backend = backend

    ft_ctx.recipes = MagicMock()
    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    ft_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_recipe_step("implement", provider="minimax"),
        "fix": _make_recipe_step("fix", provider=""),
    }
    ft_ctx.recipes.load.return_value = recipe_obj
    ft_ctx.recipes.load_and_validate.return_value = {
        "valid": True,
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
        "post_prune_step_names": ["implement"],
    }

    def _per_step_resolve_fleet(_step_name, _recipe_name, _config_providers, step_provider=""):
        return ("", None)

    with (
        patch("autoskillit.server._state._ctx", ft_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            side_effect=_per_step_resolve_fleet,
        ),
        patch(
            "autoskillit.server.tools.tools_fleet_dispatch._require_fleet",
            lambda _name: None,
        ),
    ):
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        fleet_result = await dispatch_food_truck(
            recipe="implementation",
            task="test task",
            ctx=AsyncMock(),
        )
    parsed = json.loads(fleet_result)
    assert "escape_hatch" in parsed
    assert "missing_provider_steps" in parsed

    # Cross-validation: _check_dispatch_feasibility (preflight)
    from autoskillit.server.tools._preflight import _check_dispatch_feasibility

    preflight_backend = MagicMock()
    preflight_backend.name = "codex"
    preflight_backend.capabilities = SimpleNamespace(
        anthropic_provider_capable=False,
        applicable_guards=frozenset({"some_guard"}),
    )
    preflight_step = MagicMock()
    preflight_step.tool = "run_skill"
    preflight_step.provider = ""
    preflight_steps = {"some_step": preflight_step}

    with (
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("", None),
        ),
        patch(
            "autoskillit.server.tools._preflight._get_fix_required_hook_matchers",
            return_value=["some_matcher"],
        ),
    ):
        preflight_result = _check_dispatch_feasibility(
            ["some_step"],
            preflight_steps,
            preflight_backend,
            MagicMock(),
            recipe_name="implementation",
            skill_resolver=MagicMock(),
        )
    assert preflight_result is not None
    parsed = json.loads(preflight_result)
    assert "escape_hatch" in parsed


@pytest.mark.anyio
async def test_load_recipe_partial_override_infeasible_includes_provider_guidance() -> None:
    """Test 1G: load_recipe with Codex + no provider overrides (none_pass) returns
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
        "implement": _make_recipe_step("implement", provider=""),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj

    tool_ctx.recipes.load_and_validate.return_value = {
        **_make_feasible_load_result(),
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
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
            return_value=("", None),
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
    """Test 1H: dispatch_food_truck with Codex + no provider overrides (none_pass)
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
        "implement": _make_recipe_step("implement", provider=""),
        "fix": _make_recipe_step("fix", provider=""),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": True,
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
        "post_prune_step_names": ["implement"],
    }

    with (
        patch("autoskillit.server._state._ctx", tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("", None),
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
    assert "missing_provider_steps" in parsed
    assert "escape_hatch" in parsed


def _make_git_write_step(name: str) -> MagicMock:
    """Recipe step whose skill_command resolves to a git_metadata_write skill."""
    step = _make_recipe_step(name, provider="")
    step.with_args = {"skill_command": "/autoskillit:resolve-failures"}
    return step


def _make_git_write_resolver() -> MagicMock:
    """Skill resolver whose every skill carries git_metadata_write."""
    info = MagicMock()
    info.uses_capabilities = frozenset({"git_metadata_write"})
    info.backend_requirements = frozenset({"claude-code"})
    resolver = MagicMock()
    resolver.resolve.return_value = info
    return resolver


@pytest.mark.anyio
async def test_open_kitchen_capability_route_reaches_admission_with_true_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Step 5d sibling of Test 1B: open_kitchen with Codex + NO provider overrides
    but capability route active (git_metadata_write skills + binary present) must
    reach admission with backend_supports_git_write='true' and a claude-code
    effective backend map — not the dispatch_infeasible envelope."""
    import json
    from unittest.mock import AsyncMock, patch

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from tests.server.conftest import _make_mock_ctx

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    tool_ctx = _make_mock_ctx()
    tool_ctx.gate.enabled = True
    tool_ctx.gate_infrastructure_ready = True
    tool_ctx.recipe_name = "implementation"
    tool_ctx.kitchen_id = "test-kitchen"
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    tool_ctx.temp_dir = tmp_path

    from types import SimpleNamespace

    tool_ctx.backend = MagicMock()
    tool_ctx.backend.name = "codex"
    tool_ctx.backend.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )
    tool_ctx.skill_resolver = _make_git_write_resolver()

    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_git_write_step("implement"),
        "fix": _make_git_write_step("fix"),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
    tool_ctx.recipes.load_and_validate.return_value = _make_feasible_load_result()

    fastmcp_ctx = AsyncMock()

    with (
        patch("autoskillit.server._get_ctx", return_value=tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("", None),
        ),
    ):
        result = await open_kitchen(name="implementation", ctx=fastmcp_ctx)

    parsed = json.loads(result)
    assert parsed.get("kitchen") != "dispatch_infeasible", (
        f"capability route must prevent the dispatch_infeasible refusal; got: {parsed}"
    )
    lv_kwargs = tool_ctx.recipes.load_and_validate.call_args.kwargs
    assert lv_kwargs["ingredient_overrides"]["backend_supports_git_write"] == "true"
    assert lv_kwargs["effective_backend_map"]["fix"] == "claude-code"
    assert lv_kwargs["effective_backend_map"]["implement"] == "claude-code"


@pytest.mark.anyio
async def test_load_recipe_capability_route_not_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 5d sibling of Test 1G: load_recipe with Codex + no provider overrides
    but capability route active must not return the dispatch_infeasible response,
    and must thread the capability-derived override and map into admission."""
    import json
    from unittest.mock import patch

    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server.conftest import _make_mock_ctx

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

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
    tool_ctx.skill_resolver = _make_git_write_resolver()

    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_git_write_step("implement"),
        "fix": _make_git_write_step("fix"),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
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
            return_value=("", None),
        ),
    ):
        result = await load_recipe(name="implementation")

    parsed = json.loads(result)
    assert parsed.get("dispatch_infeasible") is not True
    lv_kwargs = tool_ctx.recipes.load_and_validate.call_args.kwargs
    assert lv_kwargs["ingredient_overrides"]["backend_supports_git_write"] == "true"
    assert lv_kwargs["effective_backend_map"]["fix"] == "claude-code"


@pytest.mark.anyio
async def test_dispatch_food_truck_capability_route_no_binary_fails_closed(
    build_ctx_open: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 5d sibling of Test 1H: fleet dispatch with capability-requiring steps
    and the claude binary ABSENT keeps the fail-closed refusal — the capability
    route must not admit a fleet pipeline it cannot actually route."""
    import json
    from unittest.mock import AsyncMock, patch

    from autoskillit.core import BackendCapabilities

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: None,
    )

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
    tool_ctx.skill_resolver = _make_git_write_resolver()

    tool_ctx.recipes = MagicMock()
    recipe_info = MagicMock()
    recipe_info.path = Path("/fake/recipe.yaml")
    tool_ctx.recipes.find.return_value = recipe_info

    recipe_obj = MagicMock()
    recipe_obj.name = "implementation"
    recipe_obj.steps = {
        "implement": _make_git_write_step("implement"),
        "fix": _make_git_write_step("fix"),
    }
    tool_ctx.recipes.load.return_value = recipe_obj
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": True,
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
        "post_prune_step_names": ["implement"],
    }

    with (
        patch("autoskillit.server._state._ctx", tool_ctx),
        patch(
            "autoskillit.server._guards._resolve_provider_profile",
            return_value=("", None),
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
    lv_kwargs = tool_ctx.recipes.load_and_validate.call_args.kwargs
    assert lv_kwargs["ingredient_overrides"]["backend_supports_git_write"] == "false"


def test_provider_override_any_step_with_base_url_flips_capability() -> None:
    """Codex backend with ONE guarded step having ANTHROPIC_BASE_URL
    and all other guarded steps lacking overrides → capability flips to 'true'
    (any-suffices semantics)."""
    from unittest.mock import patch

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
        "merge_gate_fix": _make_recipe_step("merge_gate_fix", provider=""),
        "retry_worktree": _make_recipe_step("retry_worktree", provider=""),
    }

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if step_provider == "minimax":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
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
    assert result == {"backend_supports_git_write": "true"}, (
        "Any-suffices: at least one step with ANTHROPIC_BASE_URL must flip capability to 'true'"
    )
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "any_pass"


def test_guarded_step_set_matches_real_recipe() -> None:
    """Dynamically discover guarded steps from real implementation.yaml.
    The discovered set must be non-empty and reflect the real recipe structure."""
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    discovered = frozenset(
        name
        for name, step in recipe.steps.items()
        if step.skip_when_false == "inputs.backend_supports_git_write" and step.tool == "run_skill"
    )
    assert discovered, "Real implementation.yaml must have at least one guarded run_skill step"
    assert "implement" in discovered
    assert "fix" in discovered


def test_real_recipe_single_provider_override_dispatch_feasible() -> None:
    """With a real recipe and only 'implement' having ANTHROPIC_BASE_URL,
    the capability overrides must flip to 'true' (any-suffices)."""
    from unittest.mock import patch

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core import CapabilityResolutionDetail
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    recipe_obj = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    real_guarded_steps = {
        name: step
        for name, step in recipe_obj.steps.items()
        if step.skip_when_false == "inputs.backend_supports_git_write" and step.tool == "run_skill"
    }
    assert real_guarded_steps, "Real recipe must have guarded run_skill steps"

    providers = ProvidersConfig(
        profiles={"minimax": {}},
        step_overrides={"implement": "minimax"},
    )

    def _per_step_resolve(_step_name, _recipe_name, _config_providers, step_provider=""):
        if _step_name == "implement":
            return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})
        return ("", None)

    with patch(
        "autoskillit.server._guards._resolve_provider_profile",
        side_effect=_per_step_resolve,
    ):
        result, detail = _provider_aware_capability_overrides(
            _make_codex_backend(),
            "implementation",
            providers,
            real_guarded_steps,  # type: ignore[arg-type]
        )
    assert result == {"backend_supports_git_write": "true"}, (
        "Any-suffices: real recipe with one overridden step must flip to 'true'"
    )
    assert isinstance(detail, CapabilityResolutionDetail)
    assert detail.resolution_path == "any_pass"
