"""End-to-end chain tests for capability admission control.

Verifies the full chain from backend capability detection through
load_and_validate to the dispatch_feasible signal.
"""

from __future__ import annotations

from pathlib import Path

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


def test_codex_backend_produces_infeasible_recipe() -> None:
    """Codex + implementation recipe chain: backend_supports_git_write=false
    produces dispatch_feasible=False with gate_backend_write infeasible."""
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
    from autoskillit.server.tools._auto_overrides import _backend_capability_overrides

    for writable in (True, False):
        backend = MagicMock()
        backend.capabilities.git_metadata_writable = writable
        assert _backend_capability_overrides(backend) == _build_capability_overrides(backend), (
            f"Parity violation for git_metadata_writable={writable}"
        )

    assert _backend_capability_overrides(None) == _build_capability_overrides(None), (
        "Parity violation for backend=None"
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
