"""Load_recipe exception-handling tests (CC-1 outer except) and fail-closed
validation for empty / missing content.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import SkillResolver
from autoskillit.server.tools.tools_recipe import load_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


class TestLoadRecipeExceptionHandling:
    """CC-1: Outer except in load_recipe must catch anticipated exceptions only."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx_kitchen_open):
        """Initialize ToolContext with gate open so load_recipe can call _get_config()."""

    @pytest.mark.anyio
    async def test_yaml_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yaml.YAMLError is caught and returned as an error suggestion."""
        from autoskillit.core.io import YAMLError

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\n")
        with patch(
            "autoskillit.recipe._api.load_recipe_dict_with_declarations",
            side_effect=YAMLError("bad yaml"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_value_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ValueError (malformed recipe structure) is caught and returned as error suggestion."""
        from autoskillit.core.types import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test.yaml"
        recipe_path.write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        fake_match = RecipeInfo(
            name="test",
            description="Test",
            source=RecipeSource.PROJECT,
            path=recipe_path,
        )
        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=fake_match),
            patch(
                "autoskillit.recipe._api._parse_recipe", side_effect=ValueError("bad structure")
            ),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_file_not_found_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError is caught and returned as an error suggestion."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.load_recipe_card",
            side_effect=FileNotFoundError("missing"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_unexpected_exception_returns_structured_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected exceptions are caught by the handler-level exception boundary."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.run_semantic_rules",
            side_effect=AttributeError("programming error"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert result["success"] is False
        assert "error" in result
        assert "programming error" in result["error"]


class TestLoadRecipeFailClosed:
    """Fail-closed validation for empty and missing content."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        self.ctx = tool_ctx_kitchen_open

    @pytest.mark.anyio
    async def test_load_recipe_fail_closed_empty_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        _LOAD_CACHE.clear()
        test_result = {"valid": True, "content": "", "dispatch_feasible": True}
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        recipe_info = MagicMock(path=tmp_path / "test-recipe.yaml")
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: recipe_info)
        monkeypatch.setattr(
            self.ctx.recipes,
            "load",
            lambda *_a, **_kw: MagicMock(steps={}, ingredients={}),
        )
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="test-recipe")
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"].lower()

    @pytest.mark.anyio
    async def test_load_recipe_fail_closed_missing_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        _LOAD_CACHE.clear()
        test_result = {"valid": True, "dispatch_feasible": True}
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        recipe_info = MagicMock(path=tmp_path / "test-recipe.yaml")
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: recipe_info)
        monkeypatch.setattr(
            self.ctx.recipes,
            "load",
            lambda *_a, **_kw: MagicMock(steps={}, ingredients={}),
        )
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="test-recipe")
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"]

    @pytest.mark.anyio
    async def test_load_recipe_fail_closed_missing_finalized_projection(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        test_result = {
            "valid": True,
            "content": "name: test-recipe\n",
            "dispatch_feasible": True,
        }
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        recipe_info = MagicMock(path=tmp_path / "test-recipe.yaml")
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: recipe_info)
        monkeypatch.setattr(
            self.ctx.recipes,
            "load",
            lambda *_a, **_kw: MagicMock(steps={}, ingredients={}),
        )
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="test-recipe")

        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "finalized projection" in parsed["error"]
