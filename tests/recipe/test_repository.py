"""Tests for recipe/repository.py — DefaultRecipeRepository."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.core import RecipeSource
from autoskillit.core._type_results import LoadResult
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.recipe.schema import RecipeInfo

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe_info(name: str, path: Path) -> RecipeInfo:
    return RecipeInfo(
        name=name,
        description=f"Recipe: {name}",
        source=RecipeSource.PROJECT,
        path=path,
    )


def _load_result(*items: RecipeInfo) -> LoadResult[RecipeInfo]:
    return LoadResult(items=list(items))


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def test_find_returns_matching_recipe(tmp_path: Path) -> None:
    """list_recipes returns a recipe named 'foo' → find('foo', ...) returns it."""
    foo = _make_recipe_info("foo", tmp_path / "foo.yaml")
    mock_result = _load_result(foo)

    with patch("autoskillit.recipe.repository.list_recipes", return_value=mock_result):
        with patch("autoskillit.recipe.repository._dir_mtime", return_value=1.0):
            repo = DefaultRecipeRepository()
            result = repo.find("foo", tmp_path)

    assert result is foo


def test_find_returns_none_when_no_match(tmp_path: Path) -> None:
    """No recipe named 'bar' → find('bar', ...) returns None."""
    foo = _make_recipe_info("foo", tmp_path / "foo.yaml")
    mock_result = _load_result(foo)

    with patch("autoskillit.recipe.repository.list_recipes", return_value=mock_result):
        with patch("autoskillit.recipe.repository._dir_mtime", return_value=1.0):
            repo = DefaultRecipeRepository()
            result = repo.find("bar", tmp_path)

    assert result is None


# ---------------------------------------------------------------------------
# _get_list mtime caching
# ---------------------------------------------------------------------------


def test_get_list_is_cached_on_identical_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Call _get_list twice with same mtime → list_recipes called once (cache hit)."""
    mock_result = _load_result()
    call_count = {"n": 0}

    def _list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
        call_count["n"] += 1
        return mock_result

    monkeypatch.setattr("autoskillit.recipe.repository.list_recipes", _list_recipes)
    monkeypatch.setattr("autoskillit.recipe.repository._dir_mtime", lambda _: 42.0)

    repo = DefaultRecipeRepository()
    repo._get_list(tmp_path)
    repo._get_list(tmp_path)

    assert call_count["n"] == 1


def test_get_list_invalidated_on_mtime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change mtime between calls → list_recipes called twice (cache miss)."""
    mock_result = _load_result()
    call_count = {"n": 0}
    mtime_value = {"v": 1.0}

    def _list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
        call_count["n"] += 1
        return mock_result

    monkeypatch.setattr("autoskillit.recipe.repository.list_recipes", _list_recipes)
    monkeypatch.setattr("autoskillit.recipe.repository._dir_mtime", lambda _: mtime_value["v"])

    repo = DefaultRecipeRepository()
    repo._get_list(tmp_path)

    mtime_value["v"] = 2.0  # simulate directory change
    repo._get_list(tmp_path)

    assert call_count["n"] == 2


def test_get_list_invalidated_on_project_dir_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Call with dir_a then dir_b → list_recipes called twice."""
    mock_result = _load_result()
    call_count = {"n": 0}

    def _list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
        call_count["n"] += 1
        return mock_result

    monkeypatch.setattr("autoskillit.recipe.repository.list_recipes", _list_recipes)
    monkeypatch.setattr("autoskillit.recipe.repository._dir_mtime", lambda _: 1.0)

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    repo = DefaultRecipeRepository()
    repo._get_list(dir_a)
    repo._get_list(dir_b)

    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


def test_load_and_validate_delegates_to_api(tmp_path: Path) -> None:
    """load_and_validate calls _api.load_and_validate with correct args."""
    expected = {"success": True, "recipe": "data"}
    mock_api = MagicMock(return_value=expected)

    foo = _make_recipe_info("foo", tmp_path / "foo.yaml")
    with patch("autoskillit.recipe._api.load_and_validate", mock_api):
        with patch("autoskillit.recipe.repository.list_recipes", return_value=_load_result(foo)):
            with patch("autoskillit.recipe.repository._dir_mtime", return_value=1.0):
                repo = DefaultRecipeRepository()
                result = repo.load_and_validate("foo", tmp_path)

    assert result == expected
    mock_api.assert_called_once()
    call_kwargs = mock_api.call_args
    assert call_kwargs.args[0] == "foo"
    assert call_kwargs.kwargs["project_dir"] == tmp_path


def test_validate_from_path_delegates_to_api(tmp_path: Path) -> None:
    """validate_from_path calls _api.validate_from_path."""
    expected = {"valid": True}
    mock_api = MagicMock(return_value=expected)

    with patch("autoskillit.recipe._api.validate_from_path", mock_api):
        repo = DefaultRecipeRepository()
        script_path = tmp_path / "recipe.yaml"
        result = repo.validate_from_path(script_path)

    assert result == expected
    mock_api.assert_called_once_with(script_path, temp_dir_relpath=".autoskillit/temp")


def test_list_all_delegates_to_api() -> None:
    """list_all() calls _api.list_all()."""
    expected = {"items": []}
    mock_api = MagicMock(return_value=expected)

    with patch("autoskillit.recipe._api.list_all", mock_api):
        repo = DefaultRecipeRepository()
        result = repo.list_all()

    assert result == expected
    mock_api.assert_called_once_with(project_dir=None)
