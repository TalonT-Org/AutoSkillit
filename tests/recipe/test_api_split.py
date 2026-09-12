from importlib import import_module

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _frozen_names(names: str) -> frozenset[str]:
    return frozenset(names.split())


_API_MODULE_MOVES = (
    ("_api", "api._api"),
    ("_api_cache", "api._api_cache"),
    ("_api_listing", "api._api_listing"),
    ("_api_orchestration", "api_orchestration._api_orchestration"),
    ("_api_orchestration_assemble", "api_orchestration._api_orchestration_assemble"),
    ("_api_orchestration_cache", "api_orchestration._api_orchestration_cache"),
    ("_api_orchestration_match", "api_orchestration._api_orchestration_match"),
    ("_api_orchestration_parse", "api_orchestration._api_orchestration_parse"),
    ("_api_orchestration_text", "api_orchestration._api_orchestration_text"),
    ("_api_orchestration_types", "api_orchestration._api_orchestration_types"),
    ("_api_orchestration_validate", "api_orchestration._api_orchestration_validate"),
)


# Frozen before #4951 moves these modules. A forwarding shim must retain this
# star-import surface even when its canonical implementation changes location.
_EXPECTED_RUNTIME_NAMES = {
    "_api": _frozen_names(
        """
        DeferredGuard ListRecipesResult LoadCache LoadRecipeResult OpenKitchenResult RecipeInfo
        RecipeListItem SkillLister annotate_diagram_with_pruning annotations
        assert_no_raw_placeholders bind_recipe build_ingredient_rows builtin_recipes_dir
        builtin_sub_recipes_dir check_contract_staleness check_diagram_staleness
        compute_recipe_validity diagram_stale_to_suggestions filter_pruning_false_positives
        filter_version_rule find_recipe_by_name findings_to_dicts format_ingredients_table
        format_recipe_list_response list_all list_recipes load_and_validate load_recipe_card
        load_recipe_diagram load_recipe_dict_with_declarations resolve_temp_dir run_semantic_rules
        stale_to_suggestions substitute_scripts_placeholder substitute_temp_placeholder
        validate_from_path validate_recipe_cards validate_recipe_structure
        """
    ),
    "_api_cache": _frozen_names(
        """
        Any Callable LoadCache Mapping Path SessionType YamlFileCache annotations get_logger
        hashlib logger pkg_root session_type threading time
        """
    ),
    "_api_listing": _frozen_names(
        """
        Any BackendCapabilities LoadResult Path RecipeInfo RecipeListItem RuleFinding SkillLister
        YAMLError annotations build_quality_dict compute_recipe_validity
        filter_pruning_false_positives findings_to_dicts format_recipe_list_response get_logger
        list_all list_recipes load_recipe_card load_yaml logger make_validation_context
        run_semantic_rules substitute_temp_placeholder validate_from_path validate_recipe_cards
        validate_recipe_structure
        """
    ),
    "_api_orchestration": _frozen_names(
        """
        LoadRecipeResult Path Sequence TYPE_CHECKING annotations check_contract_staleness
        compute_recipe_validity findings_to_dicts get_logger list_recipes load_and_validate
        load_recipe_card load_recipe_dict_with_declarations logger pkg_root run_semantic_rules time
        validate_recipe_cards validate_recipe_structure
        """
    ),
    "_api_orchestration_assemble": _frozen_names(
        """
        Any DeferredGuard FinalizedRecipeStep LoadRecipeResult Recipe annotate_diagram_with_pruning
        annotations assert_no_raw_placeholders cast format_ingredients_table load_recipe_diagram
        """
    ),
    "_api_orchestration_cache": _frozen_names(
        """
        BackendCapabilities Path ProcessStaleError RecipeInfo Sequence SkillLister annotations
        builtin_recipes_dir dataclasses hashlib resolve_temp_dir
        """
    ),
    "_api_orchestration_match": _frozen_names(
        """
        RecipeInfo RecipeNotFoundError RecipeSource annotations find_recipe_by_name
        substitute_scripts_placeholder substitute_temp_placeholder
        """
    ),
    "_api_orchestration_parse": _frozen_names(
        "Path Recipe RecipeInfo annotations dataclasses hashlib"
    ),
    "_api_orchestration_text": _frozen_names(
        """
        ROUTING_AUTHORITY_CLAUSE Recipe STEP_SKIP_SEMANTICS_CLAUSE annotations
        build_parameter_forwarding_rules extract_sentinel_json_blocks get_logger json logger
        """
    ),
    "_api_orchestration_types": _frozen_names(
        """
        Any BackendCapabilities FinalizedRecipeProjection Path Recipe RecipeFlowEdge RecipeInfo
        RecipeStep Sequence SkillLister annotations dataclasses
        """
    ),
    "_api_orchestration_validate": _frozen_names(
        """
        Any FinalizedRecipeProjection Recipe RecipeFlowEdge RecipeStep RecipeStepGuard YAMLError
        annotations bind_recipe filter_pruning_false_positives make_validation_context
        """
    ),
}


_EXPECTED_DECLARED_ALL = {
    "_api_orchestration": [
        "_LoadPipelineInputs",
        "_ValidationResult",
        "_resolve_cache_inputs",
        "_resolve_recipe_match",
        "_parse_and_compose",
        "_run_validation_pipeline",
        "_assemble_load_result",
        "_finalize_recipe_steps",
        "_record_pipeline_error",
        "_infer_stop_failure",
        "_build_stop_step_semantics",
        "_build_orchestration_rules",
        "_canonical_string_map",
        "load_recipe_dict_with_declarations",
        "_parse_recipe",
        "load_recipe_card",
        "run_semantic_rules",
        "validate_recipe_structure",
        "list_recipes",
        "validate_recipe_cards",
        "check_contract_staleness",
        "compute_recipe_validity",
        "findings_to_dicts",
        "pkg_root",
        "_t",
        "logger",
        "load_and_validate",
    ],
    "_api_orchestration_assemble": ["_assemble_load_result", "_finalize_recipe_steps"],
    "_api_orchestration_cache": ["_canonical_string_map", "_resolve_cache_inputs"],
    "_api_orchestration_match": ["_resolve_recipe_match"],
    "_api_orchestration_parse": ["_parse_and_compose"],
    "_api_orchestration_text": [
        "_build_orchestration_rules",
        "_build_stop_step_semantics",
        "_infer_stop_failure",
    ],
    "_api_orchestration_types": ["_LoadPipelineInputs", "_ValidationResult"],
    "_api_orchestration_validate": ["_record_pipeline_error", "_run_validation_pipeline"],
}


# These private names are imported directly by current source or tests but are
# not all part of a module's public star-import surface.
_EXPLICIT_PRIVATE_IMPORTS = {
    "_api": _frozen_names("_LoadCacheEntry _build_active_recipe _compute_registry_hash"),
    "_api_cache": _frozen_names(
        """
        _LOAD_CACHE _LoadCacheEntry _MISSING _STALENESS_CACHES_CLEARED
        _check_process_staleness _clear_stale_caches _compute_registry_hash _path_mtime_ns
        _refresh_staleness_baseline
        """
    ),
}


@pytest.mark.parametrize(("legacy_name", "canonical_name"), _API_MODULE_MOVES)
def test_api_legacy_modules_preserve_frozen_surfaces(
    legacy_name: str, canonical_name: str
) -> None:
    legacy_module = import_module(f"autoskillit.recipe.{legacy_name}")
    canonical_module = import_module(f"autoskillit.recipe.{canonical_name}")

    actual_runtime_names = frozenset(
        name for name in vars(legacy_module) if not name.startswith("_")
    )
    assert actual_runtime_names == _EXPECTED_RUNTIME_NAMES[legacy_name]

    expected_all = _EXPECTED_DECLARED_ALL.get(legacy_name)
    if expected_all is None:
        assert "__all__" not in vars(legacy_module)
    else:
        assert legacy_module.__all__ == expected_all

    frozen_exports = (
        _EXPECTED_RUNTIME_NAMES[legacy_name]
        | frozenset(expected_all or ())
        | _EXPLICIT_PRIVATE_IMPORTS.get(legacy_name, frozenset())
    )
    for name in frozen_exports:
        assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_ingredients_importable_from_submodule():
    from autoskillit.recipe._recipe_ingredients import (
        format_ingredients_table,
    )

    assert callable(format_ingredients_table)


def test_composition_importable_from_submodule():
    from autoskillit.recipe._recipe_composition import _build_active_recipe

    assert callable(_build_active_recipe)


def test_analysis_graph_importable():
    from autoskillit.recipe._analysis_graph import (
        RouteEdge,
    )

    assert callable(RouteEdge)


def test_analysis_bfs_importable():
    from autoskillit.recipe._analysis_bfs import bfs_reachable

    assert callable(bfs_reachable)


def test_analysis_detectors_importable():
    from autoskillit.recipe._analysis_detectors import (
        _detect_dead_outputs,
    )

    assert callable(_detect_dead_outputs)


def test_analysis_blocks_importable():
    from autoskillit.recipe._analysis_blocks import extract_blocks

    assert callable(extract_blocks)


def test_recipe_init_surface_unchanged():
    # ValidationContext, build_recipe_graph, bfs_reachable are NOT in recipe/__init__.py
    # — they live in _analysis.py and are consumed directly by rule modules.
    from autoskillit.recipe import (
        make_validation_context,
    )

    assert callable(make_validation_context)


def test_orchestration_importable_from_submodule():
    """Issue #4860: the orchestration module is importable and exposes the phase API."""
    from autoskillit.recipe._api_orchestration import (
        _assemble_load_result,
        _resolve_cache_inputs,
        _resolve_recipe_match,
        _run_validation_pipeline,
        load_and_validate,
    )

    assert all(
        callable(f)
        for f in (
            _assemble_load_result,
            _resolve_cache_inputs,
            _resolve_recipe_match,
            _run_validation_pipeline,
            load_and_validate,
        )
    )


def test_phase_symbols_are_same_object_as_owning_shard():
    """Issue #4905: re-exports must be identity aliases, not copies."""
    from autoskillit.recipe import _api_orchestration as _orch
    from autoskillit.recipe import _api_orchestration_assemble as _assemble_mod
    from autoskillit.recipe import _api_orchestration_cache as _cache_mod
    from autoskillit.recipe import _api_orchestration_match as _match_mod
    from autoskillit.recipe import _api_orchestration_parse as _parse_mod
    from autoskillit.recipe import _api_orchestration_text as _text_mod
    from autoskillit.recipe import _api_orchestration_types as _types_mod
    from autoskillit.recipe import _api_orchestration_validate as _validate_mod

    assert _orch._resolve_cache_inputs is _cache_mod._resolve_cache_inputs
    assert _orch._resolve_recipe_match is _match_mod._resolve_recipe_match
    assert _orch._parse_and_compose is _parse_mod._parse_and_compose
    assert _orch._run_validation_pipeline is _validate_mod._run_validation_pipeline
    assert _orch._record_pipeline_error is _validate_mod._record_pipeline_error
    assert _orch._assemble_load_result is _assemble_mod._assemble_load_result
    assert _orch._finalize_recipe_steps is _assemble_mod._finalize_recipe_steps
    assert _orch._infer_stop_failure is _text_mod._infer_stop_failure
    assert _orch._build_stop_step_semantics is _text_mod._build_stop_step_semantics
    assert _orch._build_orchestration_rules is _text_mod._build_orchestration_rules
    assert _orch._LoadPipelineInputs is _types_mod._LoadPipelineInputs
    assert _orch._ValidationResult is _types_mod._ValidationResult


# Names verified by grep against tests/recipe/test_api.py and
# tests/server/test_tools_load_recipe.py — every name the test suite
# monkeypatches onto autoskillit.recipe._api_orchestration MUST be an
# attribute of the module after decomposition.
_ALL_MONKEYPATCH_TARGETS: tuple[str, ...] = (
    "load_recipe_dict_with_declarations",
    "_parse_recipe",
    "load_recipe_card",
    "run_semantic_rules",
    "validate_recipe_structure",
    "list_recipes",
    "_t",
    "logger",
    "validate_recipe_cards",
    "check_contract_staleness",
    "compute_recipe_validity",
    "findings_to_dicts",
    "pkg_root",
)


def test_monkeypatch_targets_are_module_attributes_of_api_orchestration():
    """Issue #4905: every name the test suite monkeypatches onto
    _api_orchestration must remain a module attribute after decomposition,
    so monkeypatch.setattr(orch, NAME, mock) continues to resolve at call time."""
    from autoskillit.recipe import _api_orchestration as _orch

    missing = [name for name in _ALL_MONKEYPATCH_TARGETS if not hasattr(_orch, name)]
    assert not missing, (
        f"_api_orchestration is missing monkeypatch targets {missing!r}; "
        "tests that use monkeypatch.setattr(orch, NAME, ...) will raise AttributeError"
    )


def test_analysis_graph_no_toplevel_networkx_import():
    import ast
    from pathlib import Path

    import autoskillit.recipe._analysis_graph as _analysis_graph_mod

    src_file = Path(_analysis_graph_mod.__file__)
    tree = ast.parse(src_file.read_text())
    # iter_child_nodes only visits direct children of the module node — it does not
    # recurse into nested if-blocks, so a TYPE_CHECKING-gated `import networkx as nx`
    # is invisible to this walk. That is intentional: TYPE_CHECKING guards have zero
    # runtime cost and are an accepted pattern. This test guards only bare runtime
    # top-level imports that would incur real import latency.
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "networkx", (
                    f"Top-level 'import networkx' found at line {node.lineno}; "
                    "must be a function-level import inside build_recipe_graph()"
                )
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "networkx":
            assert False, (
                f"Top-level 'from {node.module} import ...' found at line {node.lineno}; "
                "networkx must only be imported inside build_recipe_graph()"
            )
