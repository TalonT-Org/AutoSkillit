"""Tests for backend_capability_overrides injection in recipe-loading entry points.

Verifies that ``backend_supports_git_write`` is correctly derived from
``ToolContext.backend.capabilities.git_metadata_writable`` and injected into
``ingredient_overrides`` by open_kitchen, load_recipe, and the recipe://
resource handler.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import CodingAgentBackend

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_backend_with_capability(git_writable: bool) -> CodingAgentBackend:
    """Return a MagicMock that quacks like CodingAgentBackend with the given capability."""
    backend = MagicMock(spec=CodingAgentBackend)
    backend.name = "claude-code" if git_writable else "codex"
    backend.capabilities = SimpleNamespace(
        git_metadata_writable=git_writable,
        supports_tool_list_changed=True,
    )
    return backend


# ---------------------------------------------------------------------------
# _backend_capability_overrides unit tests
# ---------------------------------------------------------------------------


class TestBackendCapabilityOverrides:
    def test_none_backend_returns_true(self) -> None:
        from autoskillit.server.tools._auto_overrides import _backend_capability_overrides

        assert _backend_capability_overrides(None) == {"backend_supports_git_write": "true"}

    def test_git_writable_backend_returns_true(self) -> None:
        from autoskillit.server.tools._auto_overrides import _backend_capability_overrides

        backend = _make_backend_with_capability(True)
        assert _backend_capability_overrides(backend) == {"backend_supports_git_write": "true"}

    def test_non_writable_backend_returns_false(self) -> None:
        from autoskillit.server.tools._auto_overrides import _backend_capability_overrides

        backend = _make_backend_with_capability(False)
        assert _backend_capability_overrides(backend) == {"backend_supports_git_write": "false"}


# ---------------------------------------------------------------------------
# open_kitchen injection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestOpenKitchenInjection:
    async def test_codex_backend_injects_false(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.features = {}
        mock_ctx.config.experimental_enabled = False
        mock_ctx.backend = _make_backend_with_capability(False)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
            "ingredients_table": None,
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        mock_mcp_ctx = AsyncMock()
        mock_mcp_ctx.enable_components = AsyncMock()

        with (
            patch(
                "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("autoskillit.server._get_ctx", return_value=mock_ctx),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
            patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
        ):
            await open_kitchen(name="demo", ctx=mock_mcp_ctx)

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "false"

    async def test_claude_code_backend_injects_true(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.features = {}
        mock_ctx.config.experimental_enabled = False
        mock_ctx.backend = _make_backend_with_capability(True)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
            "ingredients_table": None,
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        mock_mcp_ctx = AsyncMock()
        mock_mcp_ctx.enable_components = AsyncMock()

        with (
            patch(
                "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("autoskillit.server._get_ctx", return_value=mock_ctx),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
            patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
        ):
            await open_kitchen(name="demo", ctx=mock_mcp_ctx)

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "true"


# ---------------------------------------------------------------------------
# load_recipe injection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestLoadRecipeInjection:
    async def test_codex_backend_injects_false(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_recipe import load_recipe

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.workspace.temp_dir = ".autoskillit/temp"
        mock_ctx.backend = _make_backend_with_capability(False)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        with (
            patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
                return_value=mock_ctx,
            ),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
        ):
            await load_recipe(name="demo")

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "false"

    async def test_claude_code_backend_injects_true(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_recipe import load_recipe

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.workspace.temp_dir = ".autoskillit/temp"
        mock_ctx.backend = _make_backend_with_capability(True)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        with (
            patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
                return_value=mock_ctx,
            ),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
        ):
            await load_recipe(name="demo")

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "true"


# ---------------------------------------------------------------------------
# get_recipe resource injection
# ---------------------------------------------------------------------------


class TestGetRecipeResourceInjection:
    def test_codex_backend_injects_false(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_kitchen import get_recipe

        mock_ctx = MagicMock()
        mock_ctx.project_dir = tmp_path
        mock_ctx.backend = _make_backend_with_capability(False)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.find.return_value = MagicMock(path=tmp_path / "demo.yaml")
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        }

        with (
            patch(
                "autoskillit.server._state._get_ctx_or_none",
                return_value=mock_ctx,
            ),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
        ):
            get_recipe("demo")

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "false"

    def test_claude_code_backend_injects_true(self, tmp_path) -> None:
        from autoskillit.server.tools.tools_kitchen import get_recipe

        mock_ctx = MagicMock()
        mock_ctx.project_dir = tmp_path
        mock_ctx.backend = _make_backend_with_capability(True)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.find.return_value = MagicMock(path=tmp_path / "demo.yaml")
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        }

        with (
            patch(
                "autoskillit.server._state._get_ctx_or_none",
                return_value=mock_ctx,
            ),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
        ):
            get_recipe("demo")

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "true"


# ---------------------------------------------------------------------------
# Merge-order: capability keys must win over caller-supplied overrides
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestOpenKitchenMergeOrderCapabilityWins:
    async def test_caller_override_clobbered_by_capability_detection(self, tmp_path) -> None:
        """When overrides={'backend_supports_git_write': 'true'} is passed but the backend
        is non-writable, the merged result must contain 'false' — the backend capability
        wins over caller overrides."""
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.features = {}
        mock_ctx.config.experimental_enabled = False
        mock_ctx.backend = _make_backend_with_capability(False)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
            "ingredients_table": None,
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        mock_mcp_ctx = AsyncMock()
        mock_mcp_ctx.enable_components = AsyncMock()

        with (
            patch(
                "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("autoskillit.server._get_ctx", return_value=mock_ctx),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
            patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
        ):
            await open_kitchen(
                name="demo",
                overrides={"backend_supports_git_write": "true"},
                ctx=mock_mcp_ctx,
            )

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "false"


@pytest.mark.anyio
class TestLoadRecipeMergeOrderCapabilityWins:
    async def test_caller_override_clobbered_by_capability_detection(self, tmp_path) -> None:
        """load_recipe must also promote backend-capability keys to the winning layer."""
        from autoskillit.server.tools.tools_recipe import load_recipe

        mock_ctx = MagicMock()
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        mock_ctx.config.migration.suppressed = []
        mock_ctx.config.workspace.temp_dir = ".autoskillit/temp"
        mock_ctx.backend = _make_backend_with_capability(False)
        mock_ctx.recipes = MagicMock()
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.temp_dir = tmp_path

        with (
            patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ),
            patch(
                "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
                return_value=mock_ctx,
            ),
            patch(
                "autoskillit.config.resolve_ingredient_defaults",
                return_value={},
            ),
            patch(
                "autoskillit.server._misc._apply_triage_gate",
                new_callable=AsyncMock,
                side_effect=lambda r, *a, **kw: r,
            ),
        ):
            await load_recipe(name="demo", overrides={"backend_supports_git_write": "true"})

        call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
        overrides = call_kwargs["ingredient_overrides"]
        assert overrides.get("backend_supports_git_write") == "false"


# ---------------------------------------------------------------------------
# Capability registry coverage: helper output matches IL-1 registry
# ---------------------------------------------------------------------------


def test_backend_capability_overrides_matches_registry():
    """The set of keys returned by _backend_capability_overrides must equal
    BACKEND_CAPABILITY_INGREDIENTS. Drift between IL-3 helper and IL-1 registry
    is a CI failure."""
    from autoskillit.config import BACKEND_CAPABILITY_INGREDIENTS
    from autoskillit.server.tools._auto_overrides import _backend_capability_overrides

    result = _backend_capability_overrides(backend=None)
    assert set(result) == BACKEND_CAPABILITY_INGREDIENTS


# ---------------------------------------------------------------------------
# Real-composition pruning via load_and_validate
# ---------------------------------------------------------------------------


class TestRealCompositionPruning:
    """REQ-PRUNE-001: real load_and_validate removes guarded steps under codex."""

    _GIT_WRITE_STEPS = {
        "implement",
        "fix",
        "merge_gate_fix",
        "retry_worktree",
        "rebase_conflict_fix",
        "resolve_review",
        "resolve_pre_review_conflicts",
        "resolve_pre_resolve_conflicts",
        "resolve_ci",
    }

    def test_codex_overrides_remove_guarded_steps_from_content(self, tmp_path: Path) -> None:
        from autoskillit.recipe._api import load_and_validate
        from autoskillit.workspace.skills import DefaultSkillResolver

        resolver = DefaultSkillResolver()
        result = load_and_validate(
            "implementation",
            project_dir=tmp_path,
            ingredient_overrides={"backend_supports_git_write": "false"},
            backend_name="codex",
            lister=resolver,
        )
        content = result["content"]
        assert content, (
            "Content must be non-empty after codex pruning — "
            "empty content indicates unrepairable dangling routes after step pruning"
        )
        for step_name in self._GIT_WRITE_STEPS:
            assert f"  {step_name}:" not in content, (
                f"Guarded step {step_name!r} still present as YAML key in pruned content"
            )
