"""Tests for recipe/repository.py — DefaultRecipeRepository."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import autoskillit.recipe.io as recipe_io
from autoskillit.core import RecipeSource
from autoskillit.core.types._type_results import LoadResult
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.recipe.schema import RecipeInfo
from tests.recipe._testing import (
    isolate_recipe_discovery_cache,
)
from tests.recipe._testing import (
    write_project_recipe as _write_project_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_recipe_discovery_caches() -> Iterator[None]:
    """Keep repository delegation tests independent of global discovery state."""
    yield from isolate_recipe_discovery_cache()


def _make_recipe_info(name: str, path: Path) -> RecipeInfo:
    return RecipeInfo(
        name=name,
        description=f"Recipe: {name}",
        source=RecipeSource.PROJECT,
        path=path,
    )


def _load_result(*items: RecipeInfo) -> LoadResult[RecipeInfo]:
    return LoadResult(items=list(items))


def _counting_list_recipes(calls: list[Path]) -> Callable[[Path], LoadResult[RecipeInfo]]:
    def delegate(project_dir: Path) -> LoadResult[RecipeInfo]:
        calls.append(project_dir)
        return recipe_io.list_recipes(project_dir)

    return delegate


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def test_find_returns_matching_recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_recipes returns a recipe named 'foo' → find('foo', ...) returns it."""
    recipe_path = _write_project_recipe(tmp_path, "foo")
    calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.recipe.repository.list_recipes", _counting_list_recipes(calls)
    )

    result = DefaultRecipeRepository().find("foo", tmp_path)

    assert result is not None
    assert result.name == "foo"
    assert result.path == recipe_path
    assert calls == [tmp_path]


def test_find_returns_none_when_no_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No recipe named 'bar' → find('bar', ...) returns None."""
    _write_project_recipe(tmp_path, "foo")
    calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.recipe.repository.list_recipes", _counting_list_recipes(calls)
    )

    result = DefaultRecipeRepository().find("bar", tmp_path)

    assert result is None
    assert calls == [tmp_path]


# ---------------------------------------------------------------------------
# Public listing delegation
# ---------------------------------------------------------------------------


def test_list_delegates_to_central_list_recipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_recipe(tmp_path, "foo")
    calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.recipe.repository.list_recipes", _counting_list_recipes(calls)
    )

    result = DefaultRecipeRepository().list(tmp_path)

    assert any(recipe.name == "foo" for recipe in result.items)
    assert calls == [tmp_path]


def test_repository_find_observes_in_place_recipe_edit(tmp_path: Path) -> None:
    recipe_path = tmp_path / ".autoskillit" / "recipes" / "recipe.yaml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text("name: before\ndescription: test\nsteps: {}\n", encoding="utf-8")
    repo = DefaultRecipeRepository()

    assert repo.find("before", tmp_path) is not None

    recipe_path.write_text("name: afterr\ndescription: test\nsteps: {}\n", encoding="utf-8")

    assert repo.find("before", tmp_path) is None
    assert repo.find("afterr", tmp_path) is not None


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


def test_load_and_validate_delegates_to_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_and_validate calls _api.load_and_validate with correct args."""
    expected = {"success": True, "recipe": "data"}
    mock_api = MagicMock(return_value=expected)

    _write_project_recipe(tmp_path, "foo")
    calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.recipe.repository.list_recipes", _counting_list_recipes(calls)
    )
    with patch("autoskillit.recipe._api.load_and_validate", mock_api):
        repo = DefaultRecipeRepository()
        result = repo.load_and_validate("foo", tmp_path)

    assert result == expected
    mock_api.assert_called_once()
    call_kwargs = mock_api.call_args
    assert call_kwargs.args[0] == "foo"
    assert call_kwargs.kwargs["project_dir"] == tmp_path
    assert calls == [tmp_path]


def test_validate_from_path_delegates_to_api(tmp_path: Path) -> None:
    """validate_from_path calls _api.validate_from_path."""
    expected = {"valid": True}
    mock_api = MagicMock(return_value=expected)

    with patch("autoskillit.recipe._api.validate_from_path", mock_api):
        repo = DefaultRecipeRepository()
        script_path = tmp_path / "recipe.yaml"
        result = repo.validate_from_path(script_path)

    assert result == expected
    mock_api.assert_called_once_with(
        script_path,
        temp_dir_relpath=".autoskillit/temp",
        backend_name=None,
        ingredient_overrides=None,
        effective_backend_map=None,
        backend_capabilities_map=None,
        backend_origin_map=None,
    )


def test_list_all_delegates_to_api() -> None:
    """list_all() calls _api.list_all()."""
    expected = {"items": []}
    mock_api = MagicMock(return_value=expected)

    with patch("autoskillit.recipe._api.list_all", mock_api):
        repo = DefaultRecipeRepository()
        result = repo.list_all()

    assert result == expected
    mock_api.assert_called_once_with(project_dir=None, features=None)


# ---------------------------------------------------------------------------
# Protocol boundary enforcement
# ---------------------------------------------------------------------------


def test_recipe_repository_protocol_find_return_type_is_recipe_info() -> None:
    """RecipeRepository.find() must declare RecipeInfo | None.

    If it returns Any, mypy cannot catch callers accessing Recipe-only attributes
    on the result — exactly the bug class this guard is designed to prevent.

    Uses string inspection because from __future__ import annotations stores
    annotations as strings at runtime.
    """
    import inspect

    from autoskillit.core.types._type_protocols_recipe import RecipeRepository

    sig = inspect.signature(RecipeRepository.find)
    ann = sig.return_annotation
    assert "RecipeInfo" in str(ann), (
        f"RecipeRepository.find() must return RecipeInfo | None. "
        f"Got: {ann!r}. "
        "Returning Any silences mypy on all callers and hides type boundary violations."
    )


def test_in_memory_recipe_repo_rejects_recipe_objects() -> None:
    """InMemoryRecipeRepository.add_recipe must only accept RecipeInfo objects.

    Accepting Recipe objects masks the production type mismatch in all dispatch tests.
    """
    from autoskillit.recipe.schema import Recipe
    from tests.fakes import InMemoryRecipeRepository

    repo = InMemoryRecipeRepository()
    with pytest.raises(TypeError, match="RecipeInfo"):
        repo.add_recipe("x", Recipe(name="x", description="x"))


def test_load_and_validate_normalizes_relative_project_dir_at_repository_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_api = MagicMock(return_value={})
    first_project = tmp_path / "first-project"
    second_project = tmp_path / "second-project"
    first_project.mkdir()
    second_project.mkdir()
    foo = _make_recipe_info("test-recipe", tmp_path / "test-recipe.yaml")
    discovered: list[Path] = []

    def list_from_project(project_dir: Path) -> LoadResult[RecipeInfo]:
        discovered.append(project_dir)
        return _load_result(foo)

    with patch("autoskillit.recipe._api.load_and_validate", mock_api):
        with patch("autoskillit.recipe.repository.list_recipes", side_effect=list_from_project):
            repo = DefaultRecipeRepository()
            monkeypatch.chdir(first_project)
            repo.load_and_validate("test-recipe", ".")
            monkeypatch.chdir(second_project)
            repo.load_and_validate("test-recipe", ".")

    assert discovered == [first_project.absolute(), second_project.absolute()]
    assert [call.kwargs["project_dir"] for call in mock_api.call_args_list] == discovered


def test_repository_load_and_validate_passes_recipe_list_to_api(tmp_path: Path) -> None:
    """DefaultRecipeRepository passes its cached recipe list to _api.load_and_validate."""
    captured_kwargs = {}

    def capturing_load_and_validate(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {}

    foo = _make_recipe_info("foo", tmp_path / "foo.yaml")

    with patch.object(
        DefaultRecipeRepository,
        "_get_list",
        return_value=_load_result(foo),
    ):
        with patch(
            "autoskillit.recipe._api.load_and_validate",
            side_effect=capturing_load_and_validate,
        ):
            repo = DefaultRecipeRepository()
            repo.load_and_validate("foo", tmp_path)

    assert "recipe_list" in captured_kwargs
    assert captured_kwargs["recipe_list"] is not None
    assert isinstance(captured_kwargs["recipe_list"], list)
    assert captured_kwargs["recipe_list"][0].name == "foo"
