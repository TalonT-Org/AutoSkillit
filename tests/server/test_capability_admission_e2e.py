"""End-to-end chain tests for capability admission control.

Verifies the full chain from backend capability detection through
load_and_validate to the dispatch_feasible signal.
"""

from __future__ import annotations

from pathlib import Path
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
        degraded = _provider_aware_capability_overrides(backend, "", None, None)
        assert degraded == _backend_capability_overrides(backend), (
            f"Graceful-degradation violation for git_metadata_writable={writable}"
        )

    degraded_none = _provider_aware_capability_overrides(None, "", None, None)
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
        result = _provider_aware_capability_overrides(
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
        result = _provider_aware_capability_overrides(
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

    result = _provider_aware_capability_overrides(
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

    result = _provider_aware_capability_overrides(
        backend,
        "implementation",
        providers,
        steps,  # type: ignore[arg-type]
    )
    assert result == {"backend_supports_git_write": "true"}
