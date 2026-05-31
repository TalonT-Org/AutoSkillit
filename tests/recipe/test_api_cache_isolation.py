"""Cache isolation tests for recipe/_api_cache.py: LoadCache copy-on-read contract."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_CACHE_TEST_RECIPE = """\
name: cache-test
description: minimal recipe for cache isolation tests
autoskillit_version: "0.3.0"
kitchen_rules:
  - "No native tools"
ingredients:
  task:
    description: The task
    required: true
steps:
  stop:
    action: stop
    message: "done"
"""


def _setup_cache_recipe(tmp_path: Path) -> Path:
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / "cache-test.yaml"
    recipe_path.write_text(_CACHE_TEST_RECIPE)
    return tmp_path


def test_load_cache_content_survives_consumer_pop(tmp_path, monkeypatch):
    """Mutating a returned result must not corrupt the cache entry."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    _setup_cache_recipe(tmp_path)

    result1 = load_and_validate("cache-test", project_dir=tmp_path)
    assert "content" in result1
    original_content = result1["content"]

    # Simulate what tools_recipe.py used to do (mutate the returned dict)
    result1.pop("content", None)
    result1.pop("orchestration_rules", None)

    # Second call must return unmutated content
    result2 = load_and_validate("cache-test", project_dir=tmp_path)
    assert "content" in result2
    assert result2["content"] == original_content


def test_load_cache_suggestions_not_aliased(tmp_path, monkeypatch):
    """Appending to returned suggestions must not affect cached entry."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    _setup_cache_recipe(tmp_path)

    result1 = load_and_validate("cache-test", project_dir=tmp_path)
    original_len = len(result1.get("suggestions", []))

    result1.setdefault("suggestions", []).append({"injected": True})

    result2 = load_and_validate("cache-test", project_dir=tmp_path)
    assert len(result2.get("suggestions", [])) == original_len


def test_load_cache_returns_distinct_objects(tmp_path, monkeypatch):
    """Each cache hit must return a new dict object, not the cached reference."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    _setup_cache_recipe(tmp_path)

    result1 = load_and_validate("cache-test", project_dir=tmp_path)
    result2 = load_and_validate("cache-test", project_dir=tmp_path)
    assert result1 is not result2
    if "suggestions" in result1 and "suggestions" in result2:
        assert result1["suggestions"] is not result2["suggestions"]


def test_copy_result_produces_independent_copy():
    """copy_result must return a dict that shares no mutable references with the input."""
    from autoskillit.recipe._api_cache import LoadCache

    cache = LoadCache()
    original = {
        "content": "hello",
        "suggestions": [{"rule": "stale-contract"}],
        "kitchen_rules": ["rule1"],
        "requires_packs": ["pack1"],
        "requires_features": ["feat1"],
        "deferred_guards": [{"step": "s1", "ingredient": "i1", "default": None}],
    }
    copy = cache.copy_result(original)

    assert copy == original
    assert copy is not original
    assert copy["suggestions"] is not original["suggestions"]
    assert copy["kitchen_rules"] is not original["kitchen_rules"]
    assert copy["requires_packs"] is not original["requires_packs"]
    assert copy["requires_features"] is not original["requires_features"]
    assert copy["deferred_guards"] is not original["deferred_guards"]

    # Mutating the copy must not affect the original
    copy["suggestions"].append({"injected": True})
    copy.pop("content", None)
    assert "content" in original
    assert len(original["suggestions"]) == 1
