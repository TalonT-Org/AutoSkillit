"""Recipe-name admission tests for model-facing recipe surfaces."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    RecipeLoadError,
    RecipeNotFoundError,
    RecipeRepository,
    SkillResolver,
)
from autoskillit.server.tools._serve_helpers import _admit_recipe_name, serve_recipe
from autoskillit.workspace import DefaultSkillResolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.parametrize(
    ("recipe_present", "skill_present", "expected_error"),
    [
        pytest.param(True, False, None, id="recipe-only"),
        pytest.param(False, True, RecipeNotFoundError, id="skill-only"),
        pytest.param(True, True, RecipeLoadError, id="ambiguous"),
        pytest.param(False, False, RecipeNotFoundError, id="neither"),
    ],
)
def test_admit_recipe_name_has_four_namespace_outcomes(
    tmp_path,
    recipe_present: bool,
    skill_present: bool,
    expected_error: type[Exception] | None,
) -> None:
    recipe_info = object() if recipe_present else None
    skill_spec = object() if skill_present else None
    recipes = MagicMock(spec=RecipeRepository)
    recipes.find.return_value = recipe_info
    resolver = MagicMock(spec=SkillResolver)
    resolver.resolve_effective.return_value = skill_spec
    ctx = SimpleNamespace(
        recipes=recipes,
        skill_resolver=resolver,
        project_dir=tmp_path,
    )

    if expected_error is None:
        assert _admit_recipe_name(ctx, "shared-name") is recipe_info
    else:
        with pytest.raises(expected_error) as raised:
            _admit_recipe_name(ctx, "shared-name")
        if skill_present and not recipe_present:
            assert "skill" in str(raised.value).lower()
            assert "current session" in str(raised.value).lower()
        elif recipe_present and skill_present:
            assert "ambiguous" in str(raised.value).lower()
            assert "rename" in str(raised.value).lower()
        else:
            assert str(raised.value) == "No recipe named 'shared-name' found"

    recipes.find.assert_called_once_with("shared-name", tmp_path)
    resolver.resolve_effective.assert_called_once_with("shared-name", tmp_path)


def test_admit_recipe_name_rejects_project_local_collision(tmp_path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "project-collision"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: project-collision\ndescription: Project collision\n---\n# Project collision\n"
    )
    recipes = MagicMock(spec=RecipeRepository)
    recipes.find.return_value = object()
    ctx = SimpleNamespace(
        recipes=recipes,
        skill_resolver=DefaultSkillResolver(),
        project_dir=tmp_path,
    )

    with pytest.raises(RecipeLoadError, match="ambiguous"):
        _admit_recipe_name(ctx, "project-collision")


def test_serve_recipe_admits_before_recipe_delivery(tmp_path) -> None:
    recipes = MagicMock(spec=RecipeRepository)
    recipes.find.return_value = None
    resolver = MagicMock(spec=SkillResolver)
    resolver.resolve_effective.return_value = object()
    ctx = SimpleNamespace(
        recipes=recipes,
        skill_resolver=resolver,
        project_dir=tmp_path,
    )

    with pytest.raises(RecipeNotFoundError, match="current session"):
        serve_recipe(
            ctx,
            "skill-only",
            caller_overrides=None,
            config_default={},
            session_overrides={},
            config_layer={},
        )

    recipes.load_and_validate.assert_not_called()
