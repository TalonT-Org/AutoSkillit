"""Load_recipe read-only invariants (P4) and authority/type gate contract
(P7 authority surface, Tier-2 type surface).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import SkillResolver
from autoskillit.recipe.schema import RecipeIngredient
from autoskillit.server.tools.tools_recipe import load_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        """Ensure server context is initialized with gate open."""

    @pytest.mark.anyio
    async def test_load_recipe_does_not_call_migration_engine(self, tmp_path, monkeypatch):
        """load_recipe must not trigger headless migration even when migrations are applicable."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=["v0.1.0"]),
            patch("autoskillit.execution.headless.run_headless_core") as mock_headless,
            patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen,
        ):
            result = json.loads(await load_recipe(name="implementation"))
        assert "error" not in result
        mock_headless.assert_not_called()
        mock_gen.assert_not_called()

    @pytest.mark.anyio
    async def test_load_recipe_does_not_auto_generate_contract_card(self, tmp_path, monkeypatch):
        """load_recipe must not call generate_recipe_card even when no card exists."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen:
            await load_recipe(name="test")
        mock_gen.assert_not_called()


class TestLoadRecipeAuthorityClobber:
    """load_recipe must reject server-authoritative overrides with a structured
    envelope before any recipe load or session snapshot mutation. The previous
    warning-based contract has been inverted: caller overrides for
    ``authority: config`` keys are no longer silently overwritten, they are
    rejected with the same envelope shape used by ``lock_ingredients``.
    """

    @pytest.mark.anyio
    async def test_load_recipe_rejects_authority_override(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from tests.server.conftest import _make_mock_ctx

        monkeypatch.chdir(tmp_path)
        mock_ctx = _make_mock_ctx()
        mock_ctx.enable_components = AsyncMock()
        mock_ctx.recipes = MagicMock()
        mock_ctx.config.migration.suppressed = []
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""

        with patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=mock_ctx,
        ):
            with patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ):
                with patch("autoskillit.server.logger"):
                    from autoskillit.server.tools.tools_recipe import load_recipe

                    result_str = await load_recipe(
                        name="demo",
                        overrides={"base_branch": "custom"},
                    )

        parsed = json.loads(result_str)
        assert parsed["success"] is False
        assert parsed["stage"] == "ingredient_authority_validation"
        assert parsed["retriable"] is False
        assert "base_branch" in parsed["error"]
        # The authority gate runs at function entry, before recipes.load is
        # called — confirms no recipe load side effect on rejection.
        mock_ctx.recipes.load.assert_not_called()
        mock_ctx.recipes.load_and_validate.assert_not_called()


class TestLoadRecipeTypeGate:
    """Tier-2 type gate: caller-supplied override values are rejected before
    any serve_recipe or session side effect runs.
    """

    @pytest.mark.anyio
    async def test_load_recipe_rejects_invalid_type_override(self, tmp_path, monkeypatch):
        """An invalid-typed override returns the type-validation envelope and
        serve_recipe must NOT have been called."""
        from unittest.mock import AsyncMock

        from tests.server.conftest import _make_mock_ctx

        monkeypatch.chdir(tmp_path)
        mock_ctx = _make_mock_ctx()
        mock_ctx.enable_components = AsyncMock()
        mock_ctx.recipes = MagicMock()
        mock_ctx.config.migration.suppressed = []
        mock_ctx.kitchen_id = "test-kitchen-type"
        mock_ctx.config.linux_tracing.log_dir = ""
        typed_recipe = SimpleNamespace(
            ingredients={"count": RecipeIngredient(description="Count", type="integer")},
            steps={"do": MagicMock()},
        )
        mock_ctx.recipes.load.return_value = typed_recipe
        mock_recipe_info = MagicMock()
        mock_recipe_info.path = "/fake/recipe.yaml"
        mock_ctx.recipes.find.return_value = mock_recipe_info

        with patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=mock_ctx,
        ):
            with patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ):
                with patch("autoskillit.server.logger"):
                    result_str = await load_recipe(
                        name="demo",
                        overrides={"count": "abc"},
                    )

        parsed = json.loads(result_str)
        assert parsed["success"] is False
        assert parsed["stage"] == "ingredient_type_validation"
        assert parsed["retriable"] is False
        assert "count" in parsed["error"]
        # serve_recipe must not be invoked when the type gate rejects.
        mock_ctx.recipes.load_and_validate.assert_not_called()
