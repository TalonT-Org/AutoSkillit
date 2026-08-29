"""Tests for recipe/_api_orchestration.py: phase function decomposition.

The orchestrator module exposes the load+validate pipeline as four named phase
functions plus the public ``load_and_validate``. Each test exercises one phase
in isolation by constructing the inputs it expects and asserting the returned
shape. End-to-end behavior is covered by ``test_api.py`` via the public re-export.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import YAMLError
from autoskillit.recipe._api_orchestration import (
    _assemble_load_result,
    _LoadPipelineInputs,
    _resolve_cache_inputs,
    _resolve_recipe_match,
    _run_validation_pipeline,
    _ValidationResult,
)
from autoskillit.recipe.schema import RecipeInfo, RecipeSource

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---- Test fixture data ----

MINIMAL_VALID_YAML = """\
name: minimal-recipe
description: minimal test recipe
kitchen_rules:
  - Use the included stop step sentinel protocol.
ingredients:
  task:
    description: A task ingredient
    required: true
    default: my-default-task-value
steps:
  done:
    action: stop
    message: Task completed successfully with full sentinel L3 block emitted for completion.
"""


def _setup_recipe(tmp_path: Path, name: str, content: str = MINIMAL_VALID_YAML) -> Path:
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = recipes_dir / f"{name}.yaml"
    recipe_path.write_text(content)
    return recipe_path


def _make_recipe_info(recipe_path: Path) -> RecipeInfo:
    return RecipeInfo(
        name=recipe_path.stem,
        description="test",
        summary="",
        version=None,
        recipe_version=None,
        content_hash="",
        content=recipe_path.read_text(),
        source=RecipeSource.PROJECT,
        path=recipe_path,
    )


def _make_inputs(name: str = "minimal-recipe", tmp_path: Path | None = None) -> dict[str, Any]:
    """Build a kwargs dict for ``_resolve_cache_inputs``/``load_and_validate``."""
    return dict(
        name=name,
        project_dir=tmp_path,
        suppressed=None,
        recipe_info=None,
        recipe_list=None,
        resolved_defaults=None,
        ingredient_overrides=None,
        temp_dir=None,
        temp_dir_relpath=None,
        lister=None,
        defer_unresolved=False,
        backend_name=None,
        effective_backend_map=None,
        backend_capabilities_map=None,
        backend_origin_map=None,
        include_finalized_projection=False,
    )


# ---- Bundle field-list tests (catches silent regression if a field is dropped) ----


def test_load_pipeline_inputs_field_list_matches_spec() -> None:
    """The bundle field set must match the spec — prevents future field drift."""
    fields = {f.name for f in dataclasses.fields(_LoadPipelineInputs)}
    expected = {
        "name",
        "pdir",
        "cache_key",
        "cacheable",
        "pkg_version",
        "rule_registry_hash",
        "project_recipes_dir",
        "builtin_dir",
        "effective_temp_dir",
        "temp_dir_relpath",
        "normalized_recipe_info",
        "recipe_list",
        "suppressed",
        "resolved_defaults",
        "ingredient_overrides",
        "lister",
        "defer_unresolved",
        "backend_name",
        "effective_backend_map",
        "backend_capabilities_map",
        "backend_origin_map",
        "include_finalized_projection",
    }
    assert fields == expected, (
        f"Bundle has drifted: missing={expected - fields}, extra={fields - expected}"
    )


def test_validation_result_field_list_matches_spec() -> None:
    """The validation result bundle field set must match the spec."""
    fields = {f.name for f in dataclasses.fields(_ValidationResult)}
    expected = {
        "match",
        "recipes_dir",
        "recipe",
        "active_recipe",
        "raw_declared",
        "raw",
        "errors",
        "suggestions",
        "skip_resolutions",
        "pre_prune_steps",
        "deferred_guard_state",
        "unreachable_step_names",
        "effective_flow_edges",
        "finalized_projection",
        "valid",
    }
    assert fields == expected, (
        f"Bundle has drifted: missing={expected - fields}, extra={fields - expected}"
    )


# ---- _resolve_cache_inputs tests ----


def test_resolve_cache_inputs_returns_load_pipeline_inputs(tmp_path: Path) -> None:
    import autoskillit.recipe._api_cache as cache_mod

    cache_mod._LOAD_CACHE.clear()

    bundle = _resolve_cache_inputs(**_make_inputs(tmp_path=tmp_path))

    assert isinstance(bundle, _LoadPipelineInputs)
    assert bundle.cacheable is True  # lister is None
    assert bundle.name == "minimal-recipe"
    assert bundle.pdir == tmp_path
    assert len(bundle.cache_key) == 25


def test_resolve_cache_inputs_sets_cacheable_false_when_lister_provided(tmp_path: Path) -> None:
    """A non-None ``lister`` must disable caching for the call."""
    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs["lister"] = object()  # any object suffices

    bundle = _resolve_cache_inputs(**kwargs)

    assert bundle.cacheable is False


def test_resolve_cache_inputs_bundles_pkg_version_and_rule_hash(tmp_path: Path) -> None:
    """pkg_version and rule_registry_hash must be threaded for the cache fast-path."""
    bundle = _resolve_cache_inputs(**_make_inputs(tmp_path=tmp_path))

    import autoskillit

    assert bundle.pkg_version == autoskillit.__version__
    # The registry hash is set by _finalize_registry() during recipe import.
    assert isinstance(bundle.rule_registry_hash, str)


def test_resolve_cache_inputs_bundles_all_backend_map_kwargs(tmp_path: Path) -> None:
    """All three backend maps reach the bundle as-is."""
    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs.update(
        effective_backend_map={"k": "v"},
        backend_capabilities_map={},
        backend_origin_map={"k2": "v2"},
    )

    bundle = _resolve_cache_inputs(**kwargs)

    assert bundle.effective_backend_map == {"k": "v"}
    assert bundle.backend_capabilities_map == {}
    assert bundle.backend_origin_map == {"k2": "v2"}


# ---- _resolve_recipe_match tests ----


def test_resolve_recipe_match_uses_normalized_recipe_info_when_provided(tmp_path: Path) -> None:
    recipe_path = _setup_recipe(tmp_path, "minimal-recipe")
    recipe_info = _make_recipe_info(recipe_path)

    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, _ = _resolve_recipe_match("minimal-recipe", bundle, t0=0.0)

    assert partial.match.path == recipe_path
    assert partial.recipe is None
    assert partial.active_recipe is None
    assert partial.valid is False


def test_resolve_recipe_match_returns_recipes_dir_for_both_sources(tmp_path: Path) -> None:
    """BUILTUP source yields pkg_root()/recipes; PROJECT source yields project dir."""
    from autoskillit.core import pkg_root

    recipe_path = _setup_recipe(tmp_path, "minimal-recipe")
    recipe_info = _make_recipe_info(recipe_path)

    # PROJECT source
    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, _ = _resolve_recipe_match("minimal-recipe", bundle, t0=0.0)
    assert partial.recipes_dir == tmp_path / ".autoskillit" / "recipes"

    # BUILTIN source
    builtin_info = RecipeInfo(
        name="built-in-recipe",
        description="b",
        summary="",
        version=None,
        recipe_version=None,
        content_hash="",
        content=MINIMAL_VALID_YAML,
        source=RecipeSource.BUILTIN,
        path=Path("/nonexistent/builtin.yaml"),
    )
    kwargs = _make_inputs(tmp_path=tmp_path, name="built-in-recipe")
    kwargs["recipe_info"] = builtin_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, _ = _resolve_recipe_match("built-in-recipe", bundle, t0=0.0)
    assert partial.recipes_dir == pkg_root() / "recipes"


# ---- _run_validation_pipeline tests ----


def test_run_validation_pipeline_returns_validation_result_on_valid_yaml(tmp_path: Path) -> None:
    recipe_path = _setup_recipe(tmp_path, "minimal-recipe")
    recipe_info = _make_recipe_info(recipe_path)

    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, t0 = _resolve_recipe_match("minimal-recipe", bundle, t0=0.0)
    result = _run_validation_pipeline(partial, bundle, t0)

    assert isinstance(result, _ValidationResult)
    assert result.recipe is not None
    assert result.active_recipe is not None
    assert result.valid is True
    assert result.errors == []


def test_run_validation_pipeline_yaml_error_caught(tmp_path: Path, monkeypatch) -> None:
    """YAMLError inside ``load_recipe_dict_with_declarations`` is caught + suggestion."""
    import autoskillit.recipe._api_orchestration as orch

    recipe_path = _setup_recipe(tmp_path, "bad-yaml")
    recipe_info = _make_recipe_info(recipe_path)

    def _boom(*_args, **_kwargs):
        raise YAMLError("bad yaml")

    monkeypatch.setattr(orch, "load_recipe_dict_with_declarations", _boom)

    kwargs = _make_inputs(tmp_path=tmp_path, name="bad-yaml")
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, t0 = _resolve_recipe_match("bad-yaml", bundle, t0=0.0)
    result = _run_validation_pipeline(partial, bundle, t0)

    assert result.valid is False
    assert any("YAML parse error" in s.get("message", "") for s in result.suggestions)


def test_run_validation_pipeline_value_error_caught(tmp_path: Path, monkeypatch) -> None:
    """ValueError inside ``load_recipe_dict_with_declarations`` is caught + suggestion."""
    import autoskillit.recipe._api_orchestration as orch

    recipe_path = _setup_recipe(tmp_path, "malformed")
    recipe_info = _make_recipe_info(recipe_path)

    def _boom(*_args, **_kwargs):
        raise ValueError("malformed")

    monkeypatch.setattr(orch, "load_recipe_dict_with_declarations", _boom)

    kwargs = _make_inputs(tmp_path=tmp_path, name="malformed")
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, t0 = _resolve_recipe_match("malformed", bundle, t0=0.0)
    result = _run_validation_pipeline(partial, bundle, t0)

    assert result.valid is False
    assert any("Invalid recipe structure" in s.get("message", "") for s in result.suggestions)


# ---- _assemble_load_result tests ----


def test_assemble_load_result_includes_all_top_level_fields(tmp_path: Path) -> None:
    """``_assemble_load_result`` populates every top-level result key."""
    recipe_path = _setup_recipe(tmp_path, "minimal-recipe")
    recipe_info = _make_recipe_info(recipe_path)

    kwargs = _make_inputs(tmp_path=tmp_path)
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, t0 = _resolve_recipe_match("minimal-recipe", bundle, t0=0.0)
    pipeline_result = _run_validation_pipeline(partial, bundle, t0)
    result = _assemble_load_result(pipeline_result, bundle)

    for key in (
        "content",
        "errors",
        "diagram",
        "suggestions",
        "valid",
        "orchestration_rules",
        "stop_step_semantics",
        "content_hash",
        "composite_hash",
        "recipe_version",
    ):
        assert key in result, f"missing key: {key}"


def test_assemble_load_result_handles_recipe_none_in_error_path(tmp_path: Path) -> None:
    """When YAML fails to parse, ``recipe=None``; assembly still produces a valid shape."""
    recipe_path = _setup_recipe(tmp_path, "missing-recipe")
    recipe_info = _make_recipe_info(recipe_path)
    recipe_info = dataclasses.replace(recipe_info, content="not: valid: yaml: [")

    kwargs = _make_inputs(tmp_path=tmp_path, name="missing-recipe")
    kwargs["recipe_info"] = recipe_info
    bundle = _resolve_cache_inputs(**kwargs)
    partial, t0 = _resolve_recipe_match("missing-recipe", bundle, t0=0.0)
    pipeline_result = _run_validation_pipeline(partial, bundle, t0)
    result = _assemble_load_result(pipeline_result, bundle)

    assert result["valid"] is False and "YAML parse error" in json.dumps(
        result.get("suggestions", [])
    )


def test_validation_pipeline_uses_orch_module_for_monkeypatch_targets(
    monkeypatch,
):
    """Issue #4905: every monkeypatchable symbol in the validate/match/parse
    shards must be looked up through _api_orchestration.{name} so
    monkeypatch.setattr(orch, NAME, mock) reaches the call sites.

    Drives load_and_validate through the public surface with a patched
    ``run_semantic_rules``; the patched version appends a sentinel to a
    captured calls list. If the validate shard bypasses the orchestrator
    attribute lookup, the patched function never runs and the test fails.
    """
    from autoskillit.recipe import _api_orchestration as _orch
    from autoskillit.recipe._api_cache import _LOAD_CACHE
    from autoskillit.recipe._api_orchestration import load_and_validate

    # Clear the load cache so the patched run_semantic_rules is exercised.
    _LOAD_CACHE.clear()

    calls: list[str] = []

    def fake_run_semantic_rules(val_ctx):
        calls.append("run_semantic_rules")
        return []

    monkeypatch.setattr(_orch, "run_semantic_rules", fake_run_semantic_rules)

    # Drive the test through the bundled "implementation" recipe so the
    # pipeline reaches the post-prune semantic-rules stage without needing
    # a hand-written recipe on disk.
    load_and_validate(
        name="implementation",
        project_dir=None,
        resolved_defaults=None,
    )
    assert "run_semantic_rules" in calls, (
        "monkeypatched run_semantic_rules was never called — the validate "
        "shard is bypassing the _api_orchestration module attribute lookup"
    )
