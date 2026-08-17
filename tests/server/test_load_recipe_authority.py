"""Load_recipe read-only invariants (P4) and authority-clobber warning contract
(P7 authority surface).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import SkillResolver
from autoskillit.server.tools.tools_recipe import load_recipe
from tests.server._helpers import _with_finalized_projection

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

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
    """load_recipe must emit an authority-clobber warning when a server-authoritative
    key is overridden by the caller. Enforcement (config-layer wins) already works;
    this test verifies the *feedback* contract.
    """

    @pytest.mark.anyio
    async def test_load_recipe_emits_authority_warning(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from tests.server.conftest import _make_mock_ctx

        monkeypatch.chdir(tmp_path)
        mock_ctx = _make_mock_ctx()
        mock_ctx.enable_components = AsyncMock()
        mock_ctx.recipes = MagicMock()
        mock_recipe_obj = MagicMock()
        mock_recipe_obj.steps = {"do": MagicMock()}
        mock_recipe_obj.ingredients = {"base_branch": MagicMock()}
        mock_ctx.recipes.load.return_value = mock_recipe_obj
        mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
            {
                "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
                "valid": True,
                "suggestions": [],
                "diagram": None,
                "ingredients_table": "--- TABLE ---",
            }
        )
        mock_ctx.recipes.find.return_value = MagicMock(path=tmp_path / "demo.yaml")
        mock_ctx.recipes.load.return_value = mock_recipe_obj
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
                    with patch(
                        "autoskillit.server.tools.tools_recipe.resolve_ingredient_defaults",
                        return_value={
                            "base_branch": "develop",
                            "is_fleet_dispatch": "false",
                            "dispatch_id": "",
                        },
                    ):
                        from autoskillit.server.tools.tools_recipe import load_recipe

                        result_str = await load_recipe(
                            name="demo",
                            overrides={"base_branch": "custom"},
                        )

        parsed = json.loads(result_str)
        warnings = parsed.get("warnings") or []
        matching = [w for w in warnings if "base_branch" in w]
        assert matching, (
            f"load_recipe must emit a warning naming base_branch; got warnings={warnings}"
        )
        server_value_match = [w for w in warnings if "server value 'develop'" in w]
        assert server_value_match, (
            "Authority-clobber warning must confirm config value won — "
            f"expected \"server value 'develop'\" in warning text; got warnings={warnings}"
        )
        caller_value_absent = [w for w in warnings if "server value 'custom'" in w]
        assert not caller_value_absent, (
            "Authority-clobber warning must NOT report the caller override as the server value — "
            f"got warnings={warnings}"
        )
