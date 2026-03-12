"""Tests for recipe/_api.py: load_and_validate kitchen_rules surface."""

from __future__ import annotations

from pathlib import Path

# Minimal recipe YAML with kitchen_rules
_RECIPE_WITH_RULES = """\
name: test-recipe-with-rules
description: A test recipe
autoskillit_version: "0.3.0"
kitchen_rules:
  - "Never use native tools"
  - "Route failures to on_failure"
ingredients:
  task:
    description: The task
    required: true
steps:
  stop:
    action: stop
    message: "done"
"""

# Minimal recipe YAML without kitchen_rules
_RECIPE_NO_RULES = """\
name: test-recipe-no-rules
description: A test recipe without rules
autoskillit_version: "0.3.0"
ingredients:
  task:
    description: The task
    required: true
steps:
  stop:
    action: stop
    message: "done"
"""


def _setup_project_recipe(tmp_path: Path, name: str, content: str) -> Path:
    """Write a recipe YAML to tmp_path/.autoskillit/recipes/<name>.yaml."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / f"{name}.yaml"
    recipe_path.write_text(content)
    return recipe_path


# T4a
def test_load_and_validate_includes_kitchen_rules(tmp_path):
    """Response has top-level 'kitchen_rules' key with rule strings."""
    from autoskillit.recipe._api import load_and_validate

    _setup_project_recipe(tmp_path, "test-recipe-with-rules", _RECIPE_WITH_RULES)
    result = load_and_validate("test-recipe-with-rules", project_dir=tmp_path)

    assert "kitchen_rules" in result, "kitchen_rules should be present when recipe has rules"
    assert isinstance(result["kitchen_rules"], list)
    assert len(result["kitchen_rules"]) == 2
    assert "Never use native tools" in result["kitchen_rules"]


# T4b
def test_load_and_validate_omits_kitchen_rules_when_empty(tmp_path):
    """Response has no 'kitchen_rules' key when recipe has none."""
    from autoskillit.recipe._api import load_and_validate

    _setup_project_recipe(tmp_path, "test-recipe-no-rules", _RECIPE_NO_RULES)
    result = load_and_validate("test-recipe-no-rules", project_dir=tmp_path)

    assert "kitchen_rules" not in result, "kitchen_rules should be absent when recipe has none"


# ---------------------------------------------------------------------------
# Minimal recipe fixture for cache tests
# ---------------------------------------------------------------------------

MINIMAL_RECIPE_YAML = """\
name: myrecipe
description: minimal test recipe
autoskillit_version: "0.2.0"
kitchen_rules:
  - Never use native tools
steps:
  stop:
    action: stop
    message: done
"""


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_load_and_validate_returns_cached_result_on_second_call(tmp_path, monkeypatch):
    """Second call for unchanged recipe returns cached result without re-running pipeline."""
    import autoskillit.recipe._api as api_mod

    api_mod._LOAD_CACHE.clear()

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_yaml = recipes_dir / "myrecipe.yaml"
    recipe_yaml.write_text(MINIMAL_RECIPE_YAML)

    calls = []
    real_validate = api_mod.validate_recipe

    def counting_validate(recipe):
        calls.append(1)
        return real_validate(recipe)

    monkeypatch.setattr(api_mod, "validate_recipe", counting_validate)

    api_mod.load_and_validate("myrecipe", tmp_path)
    api_mod.load_and_validate("myrecipe", tmp_path)

    assert len(calls) == 1  # validate_recipe called only once across two loads


def test_load_and_validate_cache_invalidated_on_recipe_mtime_change(tmp_path, monkeypatch):
    """Changing the recipe file mtime causes a cache miss."""
    import autoskillit.recipe._api as api_mod

    api_mod._LOAD_CACHE.clear()

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_yaml = recipes_dir / "myrecipe.yaml"
    recipe_yaml.write_text(MINIMAL_RECIPE_YAML)

    calls = []
    real_validate = api_mod.validate_recipe

    def counting_validate(recipe):
        calls.append(1)
        return real_validate(recipe)

    monkeypatch.setattr(api_mod, "validate_recipe", counting_validate)

    api_mod.load_and_validate("myrecipe", tmp_path)
    recipe_yaml.touch()
    api_mod.load_and_validate("myrecipe", tmp_path)

    assert len(calls) == 2  # both calls ran full pipeline


def test_load_and_validate_cache_invalidated_on_pkg_version_change(tmp_path, monkeypatch):
    """Package version change invalidates the cache."""
    import autoskillit.recipe._api as api_mod

    api_mod._LOAD_CACHE.clear()

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "myrecipe.yaml").write_text(MINIMAL_RECIPE_YAML)

    calls = []
    real_validate = api_mod.validate_recipe

    def counting_validate(recipe):
        calls.append(1)
        return real_validate(recipe)

    monkeypatch.setattr(api_mod, "validate_recipe", counting_validate)

    api_mod.load_and_validate("myrecipe", tmp_path)
    monkeypatch.setattr(api_mod, "_get_pkg_version", lambda: "99.99.99")
    api_mod.load_and_validate("myrecipe", tmp_path)

    assert len(calls) == 2


def test_load_and_validate_cache_invalidated_on_dir_mtime_change(tmp_path, monkeypatch):
    """Adding a new recipe file to the project directory invalidates the cache."""
    import autoskillit.recipe._api as api_mod

    api_mod._LOAD_CACHE.clear()

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "myrecipe.yaml").write_text(MINIMAL_RECIPE_YAML)

    calls = []
    real_validate = api_mod.validate_recipe

    def counting_validate(recipe):
        calls.append(1)
        return real_validate(recipe)

    monkeypatch.setattr(api_mod, "validate_recipe", counting_validate)

    api_mod.load_and_validate("myrecipe", tmp_path)
    (recipes_dir / "newrecipe.yaml").write_text(
        MINIMAL_RECIPE_YAML.replace("myrecipe", "newrecipe")
    )
    api_mod.load_and_validate("myrecipe", tmp_path)

    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Stage timing test
# ---------------------------------------------------------------------------


def test_load_and_validate_logs_stage_timing_at_debug(tmp_path, monkeypatch):
    """load_and_validate calls the timing helper for each pipeline stage."""
    import autoskillit.recipe._api as api_mod

    api_mod._LOAD_CACHE.clear()

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "myrecipe.yaml").write_text(MINIMAL_RECIPE_YAML)

    stage_calls: list[str] = []
    real_t = api_mod._t

    def capturing_t(label: str, t0: float, name: str) -> float:
        stage_calls.append(label)
        return real_t(label, t0, name)

    monkeypatch.setattr(api_mod, "_t", capturing_t)
    api_mod.load_and_validate("myrecipe", tmp_path)

    # At minimum: find_recipe, yaml_parse, validate_recipe, semantic_rules
    assert len(stage_calls) >= 4
    assert "find_recipe" in stage_calls
    assert "yaml_parse" in stage_calls
    assert "validate_recipe" in stage_calls
    assert "semantic_rules" in stage_calls


# ---------------------------------------------------------------------------
# T-TYPED-1: LoadRecipeResult TypedDict contract test
# ---------------------------------------------------------------------------


def test_load_recipe_result_is_typed() -> None:
    """T-TYPED-1: LoadRecipeResult TypedDict must be importable from recipe._api.

    Fails until LoadRecipeResult is defined. Once passing, mypy can enforce the schema
    at all call sites that use the return type annotation.
    """
    from autoskillit.recipe._api import LoadRecipeResult  # fails until defined

    assert LoadRecipeResult is not None
    # Verify required keys are declared — use get_type_hints for robust introspection
    # (handles inherited keys if the TypedDict is later split into base+extension).
    import typing  # noqa: PLC0415

    hints = typing.get_type_hints(LoadRecipeResult)
    assert "content" in hints
    assert "diagram" in hints
    assert "suggestions" in hints
    assert "valid" in hints


# ---------------------------------------------------------------------------
# Repository routing test
# ---------------------------------------------------------------------------


def test_repository_load_and_validate_passes_recipe_info_to_api(monkeypatch):
    """DefaultRecipeRepository.load_and_validate passes a pre-resolved RecipeInfo to _api."""
    from autoskillit.recipe import _api as api_mod
    from autoskillit.recipe.repository import DefaultRecipeRepository

    captured = {}
    real_fn = api_mod.load_and_validate

    def capturing_fn(name, project_dir, *, suppressed=None, recipe_info=None):
        captured["recipe_info"] = recipe_info
        return real_fn(name, project_dir, suppressed=suppressed, recipe_info=recipe_info)

    monkeypatch.setattr(api_mod, "load_and_validate", capturing_fn)

    repo = DefaultRecipeRepository()
    repo.load_and_validate("smoke-test", Path.cwd())

    assert captured.get("recipe_info") is not None
    assert captured["recipe_info"].name == "smoke-test"
