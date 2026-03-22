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

    def capturing_fn(
        name,
        project_dir,
        *,
        suppressed=None,
        recipe_info=None,
        resolved_defaults=None,
        ingredient_overrides=None,
    ):
        captured["recipe_info"] = recipe_info
        return real_fn(
            name,
            project_dir,
            suppressed=suppressed,
            recipe_info=recipe_info,
            resolved_defaults=resolved_defaults,
            ingredient_overrides=ingredient_overrides,
        )

    monkeypatch.setattr(api_mod, "load_and_validate", capturing_fn)

    repo = DefaultRecipeRepository()
    repo.load_and_validate("smoke-test", Path.cwd())

    assert captured.get("recipe_info") is not None
    assert captured["recipe_info"].name == "smoke-test"


# ---------------------------------------------------------------------------
# Ingredient sort order enforcement
# ---------------------------------------------------------------------------


class TestIngredientSortOrder:
    """Ingredients must sort: required > auto-detect > flags > constants > optional."""

    def test_sort_key_required_is_highest_priority(self):
        from autoskillit.recipe._api import _ingredient_sort_key

        key = _ingredient_sort_key("task", required=True, default=None)
        assert key[0] == 0

    def test_sort_key_auto_detect_above_flags(self):
        from autoskillit.recipe._api import _ingredient_sort_key

        auto = _ingredient_sort_key("source_dir", required=False, default="")
        flag = _ingredient_sort_key("audit", required=False, default="true")
        assert auto[0] < flag[0], "auto-detect must sort above boolean flags"

    def test_sort_key_flags_above_optional(self):
        from autoskillit.recipe._api import _ingredient_sort_key

        flag = _ingredient_sort_key("audit", required=False, default="true")
        opt = _ingredient_sort_key("issue_url", required=False, default=None)
        assert flag[0] < opt[0], "boolean flags must sort above optional"

    def test_sort_key_optional_above_constants(self):
        from autoskillit.recipe._api import _ingredient_sort_key

        opt = _ingredient_sort_key("issue_url", required=False, default=None)
        const = _ingredient_sort_key("run_name", required=False, default="impl")
        assert opt[0] < const[0], "optional must sort above constants (rarely changed)"

    def test_sort_key_full_tier_ordering(self):
        """All five tiers must be strictly ordered."""
        from autoskillit.recipe._api import _ingredient_sort_key

        tiers = [
            _ingredient_sort_key("task", required=True, default=None)[0],  # required
            _ingredient_sort_key("source_dir", required=False, default="")[0],  # auto-detect
            _ingredient_sort_key("audit", required=False, default="true")[0],  # flag
            _ingredient_sort_key("issue_url", required=False, default=None)[0],  # optional
            _ingredient_sort_key("run_name", required=False, default="impl")[0],  # constant
        ]
        assert tiers == sorted(tiers), f"Tiers must be strictly ascending: {tiers}"
        assert len(set(tiers)) == 5, f"All 5 tiers must be distinct: {tiers}"

    def test_implementation_table_has_required_first(self):
        """Implementation recipe must show required ingredients at the top."""
        from autoskillit.core import load_yaml
        from autoskillit.recipe._api import format_ingredients_table
        from autoskillit.recipe.io import _parse_recipe, find_recipe_by_name

        match = find_recipe_by_name("implementation", Path.cwd())
        assert match is not None
        data = load_yaml(match.path.read_text())
        recipe = _parse_recipe(data)
        table = format_ingredients_table(recipe)
        assert table is not None
        lines = [
            ln for ln in table.splitlines() if "|" in ln and "---" not in ln and "Name" not in ln
        ]
        # First data row must be the required ingredient (task *)
        assert "task *" in lines[0], f"First row must be 'task *', got: {lines[0]}"

    def test_merge_prs_table_has_auto_detect_before_flags(self):
        """merge-prs recipe must show auto-detect ingredients before boolean flags."""
        from autoskillit.core import load_yaml
        from autoskillit.recipe._api import format_ingredients_table
        from autoskillit.recipe.io import _parse_recipe, find_recipe_by_name

        match = find_recipe_by_name("merge-prs", Path.cwd())
        assert match is not None
        data = load_yaml(match.path.read_text())
        recipe = _parse_recipe(data)
        table = format_ingredients_table(recipe)
        assert table is not None
        lines = [
            ln for ln in table.splitlines() if "|" in ln and "---" not in ln and "Name" not in ln
        ]
        names = [ln.split("|")[1].strip() for ln in lines]
        # base_branch and source_dir (auto-detect) must appear before audit (flag)
        base_idx = next(i for i, n in enumerate(names) if "base_branch" in n)
        source_idx = next(i for i, n in enumerate(names) if "source_dir" in n)
        audit_idx = next(i for i, n in enumerate(names) if "audit" in n)
        assert base_idx < audit_idx, f"base_branch ({base_idx}) must be before audit ({audit_idx})"
        assert source_idx < audit_idx, (
            f"source_dir ({source_idx}) must be before audit ({audit_idx})"
        )


def test_build_ingredient_rows_returns_tuples():
    """_build_ingredient_rows must return a list of (name, description, default) tuples
    with full (uncapped) description strings — the terminal renderer, not this function,
    is responsible for truncation."""
    from autoskillit.core import pkg_root
    from autoskillit.recipe._api import _build_ingredient_rows
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe

    recipes_dir = pkg_root() / "recipes"
    recipe_info = find_recipe_by_name("implementation", recipes_dir)
    assert recipe_info is not None
    recipe = load_recipe(recipe_info.path)
    rows = _build_ingredient_rows(recipe, resolved_defaults={})
    assert all(isinstance(r, tuple) and len(r) == 3 for r in rows)
    # Full descriptions must be present (not truncated at this layer)
    all_descs = [r[1] for r in rows]
    assert any(len(d) > 60 for d in all_descs), "Expected at least one long description"
