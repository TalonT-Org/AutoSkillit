import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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
