"""Tests for autoskillit server list_recipes tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_recipe import list_recipes

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestListRecipeTools:
    """Tests for kitchen-gated list_recipes tool."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx):
        """Ensure server context is initialized (gate open by default)."""

    # SS1
    @pytest.mark.anyio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_returns_json_object(self, mock_list):
        """list_recipes returns JSON object with scripts array."""
        from autoskillit.core.types import LoadResult, RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        mock_list.return_value = LoadResult(
            items=[
                RecipeInfo(
                    name="impl",
                    description="Implement",
                    summary="plan > impl",
                    path=Path("/x"),
                    source=RecipeSource.PROJECT,
                ),
            ],
            errors=[],
        )
        result = json.loads(await list_recipes())
        assert isinstance(result, dict)
        assert len(result["recipes"]) == 1
        assert result["recipes"][0]["name"] == "impl"
        assert result["recipes"][0]["description"] == "Implement"
        assert result["recipes"][0]["summary"] == "plan > impl"
        assert "errors" not in result

    # SS4
    @pytest.mark.anyio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_reports_errors_in_response(self, mock_list):
        """list_recipes includes errors in JSON when recipes fail to parse."""
        from autoskillit.core.types import LoadReport, LoadResult

        mock_list.return_value = LoadResult(
            items=[],
            errors=[LoadReport(path=Path("/recipes/broken.yaml"), error="bad yaml")],
        )
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1
        assert result["errors"][0]["file"] == "broken.yaml"
        assert "bad yaml" in result["errors"][0]["error"]

    # SS5
    @pytest.mark.anyio
    async def test_list_integration_discovers_project_recipe(self, tmp_path, monkeypatch):
        """Server tool returns project recipes alongside bundled recipes."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "pipeline.yaml").write_text(
            "name: test-pipe\ndescription: Test\nsummary: a > b\n"
            "steps:\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "test-pipe" in names

    # SS6
    @pytest.mark.anyio
    async def test_list_integration_reports_errors(self, tmp_path, monkeypatch):
        """Server tool reports parse errors to the caller from real files."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "broken.yaml").write_text("[unclosed bracket\n")
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1

    # SS8
    @pytest.mark.anyio
    async def test_list_recipes_includes_builtins_with_empty_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_recipes MCP returns bundled recipes when .autoskillit/recipes/ is absent."""
        monkeypatch.chdir(tmp_path)
        # No .autoskillit/recipes/ created — simulates a fresh project with no local recipes
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "implementation" in names
        assert "remediation" in names
        assert "smoke-test" not in names

    # SS10
    @pytest.mark.anyio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_recipes_response_includes_source_field(self, mock_list):
        """list_recipes MCP response must include source field for each recipe entry."""
        from autoskillit.core.types import LoadResult, RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        mock_list.return_value = LoadResult(
            items=[
                RecipeInfo(
                    name="impl",
                    description="Implement",
                    source=RecipeSource.BUILTIN,
                    path=Path("/recipes/impl.yaml"),
                ),
                RecipeInfo(
                    name="my-recipe",
                    description="Custom",
                    source=RecipeSource.PROJECT,
                    path=Path("/project/my-recipe.yaml"),
                ),
            ],
            errors=[],
        )
        result = json.loads(await list_recipes())
        assert "source" in result["recipes"][0], (
            "MCP list_recipes response must include 'source' field"
        )
        assert result["recipes"][0]["source"] == "builtin"
        assert result["recipes"][1]["source"] == "project"


# ---------------------------------------------------------------------------
# P5F2: Accessor pattern tests (gated tools use _get_ctx_or_none after gate check)
# ---------------------------------------------------------------------------


# P5F2-T1
@pytest.mark.anyio
async def test_list_recipes_no_recipes_returns_empty(tool_ctx):
    """list_recipes returns error JSON when recipes is not configured."""
    tool_ctx.recipes = None
    result = json.loads(await list_recipes())
    assert isinstance(result, dict) and "error" in result


# P5F2-T1b
@pytest.mark.anyio
async def test_list_recipes_gate_closed_returns_gate_error(tool_ctx):
    """list_recipes returns gate_error JSON when kitchen gate is closed."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await list_recipes())
    assert result.get("success") is False
    assert result.get("subtype") == "gate_error"
