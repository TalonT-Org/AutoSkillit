"""Tests for JSON pre-compilation fast path in recipe I/O."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from autoskillit.core import RecipeSource
from autoskillit.recipe.io import (
    _collect_recipes,
    _load_recipe_dict,
    builtin_recipes_dir,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_MINIMAL_RECIPE = {
    "name": "test-recipe",
    "description": "A test recipe",
    "steps": {
        "done": {"action": "stop", "message": "Done."},
    },
}


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


def _compile_json(yaml_path: Path) -> None:
    """Inline compilation: YAML -> JSON sibling."""
    data = yaml.safe_load(yaml_path.read_bytes())
    json_path = yaml_path.with_suffix(".json")
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_load_recipe_dict_prefers_json_when_fresh(tmp_path, monkeypatch):
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, _MINIMAL_RECIPE)
    _compile_json(yaml_path)
    # Ensure JSON mtime is strictly greater than YAML mtime (required by > freshness gate)
    json_path = yaml_path.with_suffix(".json")
    future_mtime_ns = yaml_path.stat().st_mtime_ns + 10_000_000_000
    os.utime(json_path, ns=(future_mtime_ns, future_mtime_ns))

    load_yaml_calls = []
    monkeypatch.setattr(
        "autoskillit.recipe.io.load_yaml",
        lambda *a, **kw: load_yaml_calls.append(1) or _MINIMAL_RECIPE,
    )

    result = _load_recipe_dict(yaml_path)

    assert result == _MINIMAL_RECIPE
    assert load_yaml_calls == [], "load_yaml should not be called when JSON is fresh"


def test_load_recipe_dict_falls_back_when_json_missing(tmp_path, monkeypatch):
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, _MINIMAL_RECIPE)

    load_yaml_calls = []
    monkeypatch.setattr(
        "autoskillit.recipe.io.load_yaml",
        lambda *a, **kw: load_yaml_calls.append(1) or _MINIMAL_RECIPE,
    )

    result = _load_recipe_dict(yaml_path)

    assert result == _MINIMAL_RECIPE
    assert load_yaml_calls == [1], "load_yaml should be called when JSON sibling is absent"


def test_load_recipe_dict_falls_back_when_json_stale(tmp_path, monkeypatch):
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, _MINIMAL_RECIPE)
    json_path = yaml_path.with_suffix(".json")

    _compile_json(yaml_path)
    # Manually set JSON mtime to the past so YAML is newer
    js_mtime_ns = json_path.stat().st_mtime_ns
    os.utime(json_path, ns=(js_mtime_ns - 10_000_000_000, js_mtime_ns - 10_000_000_000))

    load_yaml_calls = []
    monkeypatch.setattr(
        "autoskillit.recipe.io.load_yaml",
        lambda *a, **kw: load_yaml_calls.append(1) or _MINIMAL_RECIPE,
    )

    result = _load_recipe_dict(yaml_path)

    assert result == _MINIMAL_RECIPE
    assert load_yaml_calls == [1], "load_yaml should be called when JSON is stale"


def test_load_recipe_dict_applies_substitution_on_json(tmp_path):
    recipe_with_placeholder = {
        "name": "test-recipe",
        "description": "A test",
        "steps": {
            "run": {
                "tool": "test_check",
                "with": {"worktree_path": "{{AUTOSKILLIT_TEMP}}"},
            },
        },
    }
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, recipe_with_placeholder)
    _compile_json(yaml_path)
    json_path = yaml_path.with_suffix(".json")
    future_mtime_ns = yaml_path.stat().st_mtime_ns + 10_000_000_000
    os.utime(json_path, ns=(future_mtime_ns, future_mtime_ns))

    result = _load_recipe_dict(yaml_path, temp_dir_relpath="custom/temp")

    assert result["steps"]["run"]["with"]["worktree_path"] == "custom/temp"


def test_load_recipe_dict_handles_json_decode_error(tmp_path, monkeypatch):
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, _MINIMAL_RECIPE)
    json_path = yaml_path.with_suffix(".json")
    # Write corrupt content with an explicitly future mtime so the freshness gate passes
    json_path.write_text("{ invalid json }", encoding="utf-8")
    future_mtime_ns = yaml_path.stat().st_mtime_ns + 10_000_000_000
    os.utime(json_path, ns=(future_mtime_ns, future_mtime_ns))

    load_yaml_calls = []
    monkeypatch.setattr(
        "autoskillit.recipe.io.load_yaml",
        lambda *a, **kw: load_yaml_calls.append(1) or _MINIMAL_RECIPE,
    )

    result = _load_recipe_dict(yaml_path)

    assert result == _MINIMAL_RECIPE
    assert load_yaml_calls == [1], "load_yaml should be called when JSON is corrupt"


def test_load_recipe_dict_falls_back_when_json_is_not_mapping(tmp_path, monkeypatch):
    yaml_path = tmp_path / "recipe.yaml"
    _write_yaml(yaml_path, _MINIMAL_RECIPE)
    json_path = yaml_path.with_suffix(".json")
    # Write valid JSON that is a list (not a mapping) with a future mtime
    json_path.write_text("[1, 2, 3]\n", encoding="utf-8")
    future_mtime_ns = yaml_path.stat().st_mtime_ns + 10_000_000_000
    os.utime(json_path, ns=(future_mtime_ns, future_mtime_ns))

    load_yaml_calls = []
    monkeypatch.setattr(
        "autoskillit.recipe.io.load_yaml",
        lambda *a, **kw: load_yaml_calls.append(1) or _MINIMAL_RECIPE,
    )

    result = _load_recipe_dict(yaml_path)

    assert result == _MINIMAL_RECIPE
    assert load_yaml_calls == [1], "load_yaml should be called when JSON is not a mapping"


def test_collect_recipes_identical_with_json(tmp_path):
    recipes = [
        {"name": "recipe-a", "description": "A", "steps": {"done": {"action": "stop"}}},
        {"name": "recipe-b", "description": "B", "steps": {"done": {"action": "stop"}}},
        {"name": "recipe-c", "description": "C", "steps": {"done": {"action": "stop"}}},
    ]
    for r in recipes:
        _write_yaml(tmp_path / f"{r['name']}.yaml", r)

    # Collect without JSON siblings
    seen1: set[str] = set()
    items1: list = []
    errors1: list = []
    _collect_recipes(RecipeSource.PROJECT, tmp_path, seen1, items1, errors1)

    # Compile JSON siblings and set future mtime so the fast path is exercised
    for r in recipes:
        yaml_path = tmp_path / f"{r['name']}.yaml"
        _compile_json(yaml_path)
        json_path = yaml_path.with_suffix(".json")
        future_mtime_ns = yaml_path.stat().st_mtime_ns + 10_000_000_000
        os.utime(json_path, ns=(future_mtime_ns, future_mtime_ns))

    # Collect with JSON siblings
    seen2: set[str] = set()
    items2: list = []
    errors2: list = []
    _collect_recipes(RecipeSource.PROJECT, tmp_path, seen2, items2, errors2)

    # Assert identical name/description/version/kind
    for a, b in zip(sorted(items1, key=lambda x: x.name), sorted(items2, key=lambda x: x.name)):
        assert a.name == b.name
        assert a.description == b.description
        assert a.version == b.version
        assert a.kind == b.kind


def test_compile_recipes_roundtrip():
    for yaml_path in sorted(builtin_recipes_dir().rglob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_bytes())
        json_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        roundtripped = json.loads(json_text)
        assert roundtripped == data, f"Roundtrip failed for {yaml_path.name}"


def test_bundled_json_files_are_fresh():
    for yaml_path in sorted(builtin_recipes_dir().rglob("*.yaml")):
        json_path = yaml_path.with_suffix(".json")
        assert json_path.exists(), f"Missing JSON sibling for {yaml_path.name}"

        yaml_data = yaml.safe_load(yaml_path.read_bytes())
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        assert json_data == yaml_data, f"JSON is stale for {yaml_path.name}"
        assert json_path.stat().st_mtime_ns > yaml_path.stat().st_mtime_ns, (
            f"JSON mtime is older than YAML mtime for {yaml_path.name}"
            " — fast-path would be bypassed"
        )


def test_compile_recipes_skips_unchanged_files(tmp_path):
    """_compile_one must not rewrite JSON when content is unchanged."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    from compile_recipes import _compile_one

    # Create a temp YAML file with known content
    yaml_content = {"name": "test-recipe", "version": "1.0"}
    yaml_file = tmp_path / "test-recipe.yaml"
    yaml_file.write_text("name: test-recipe\nversion: '1.0'\n")

    # First compile
    result1 = _compile_one(yaml_file)
    assert result1 is True, "First compile should return True (file written)"
    json_file = yaml_file.with_suffix(".json")
    assert json_file.exists()
    mtime1 = json_file.stat().st_mtime_ns

    # Second compile — content unchanged
    result2 = _compile_one(yaml_file)
    assert result2 is False, "Second compile with unchanged content should return False"
    mtime2 = json_file.stat().st_mtime_ns
    assert mtime1 == mtime2, "mtime must not change when content is unchanged"
