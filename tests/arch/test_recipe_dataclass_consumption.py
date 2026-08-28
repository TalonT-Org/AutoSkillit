"""Owner-qualified recipe-dataclass consumption ledger.

This is deliberately a finite AST scan, not whole-program type inference.  It
proves a declared source surface contains an attribute load and that the named
consumer function resolves, but it cannot prove control-flow reachability or
the runtime owner of a syntactically identical attribute access.  Keeping the
ledger owner-qualified, limiting the source surface, and requiring the
documented category-level behavioral test anchors to resolve at their paths
make that limitation explicit.  Per-field attribution — does the test
actually exercise *this* field — is delegated to the per-field behavioral
coverage in the existing recipe/IO/validator test suites; this ledger stops
at the cheaper category-level liveness check.  The shared anchors
(``_BEHAVIORAL_ANCHOR`` for execution, ``_COMPOSITION_ANCHOR`` for
composition) cover category-level liveness rather than per-field attribution.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterable
from pathlib import Path

import pytest

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from tests.arch._deferred_debt import (
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)
from tests.arch._recipe_field_ledger import (
    _BEHAVIORAL_ANCHOR,
    _EXECUTION_SITE,
    DECLARED_RECIPE_FIELDS,
    DEFERRED_RECIPE_FIELDS,
    DeclaredFieldDef,
    FieldKey,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PRODUCTION_MODULES = (
    "autoskillit.recipe._analysis",
    "autoskillit.recipe._api_orchestration",
    # Issue #4905: composite_hash/content_hash attribute loads moved from the
    # public-driver facade into the assemble and parse shards during the
    # _api_orchestration decomposition. The remaining shards are tracked for
    # symmetry with the new module surface so future field moves are visible
    # to the ledger.
    "autoskillit.recipe._api_orchestration_assemble",
    "autoskillit.recipe._api_orchestration_cache",
    "autoskillit.recipe._api_orchestration_match",
    "autoskillit.recipe._api_orchestration_parse",
    "autoskillit.recipe._api_orchestration_text",
    "autoskillit.recipe._api_orchestration_types",
    "autoskillit.recipe._api_orchestration_validate",
    "autoskillit.recipe._io_loading",
    "autoskillit.recipe._recipe_composition",
    "autoskillit.recipe.io",
    "autoskillit.recipe.validator",
    "autoskillit.recipe.rules.dataflow.rules_dataflow",
    "autoskillit.recipe.rules.dataflow.rules_dataflow_handoff",
    "autoskillit.recipe.rules.graph.rules_graph_output",
    "autoskillit.recipe.rules.graph.rules_graph_review",
    "autoskillit.recipe.rules.graph.rules_graph_routes",
    "autoskillit.recipe.rules.graph.rules_graph_summary",
    "autoskillit.recipe.rules.rules_actions",
    "autoskillit.recipe.rules.rules_blocks",
    "autoskillit.recipe.rules.rules_bypass",
    "autoskillit.recipe.rules.rules_features",
    "autoskillit.recipe.rules.rules_model",
    "autoskillit.recipe.rules.rules_optional_capture",
    "autoskillit.recipe.rules.rules_packs",
    "autoskillit.recipe.rules.rules_phoropter_adjacency",
    "autoskillit.recipe.rules.rules_reachability",
    "autoskillit.server._recipe_execution",
    "autoskillit.server.tools.tools_execution._run_skill_admission",
    "autoskillit.server.tools.tools_execution._run_skill_prepare",
    "autoskillit.server.tools.tools_recipe",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _module_path(module: str) -> Path:
    return _project_root() / "src" / Path(*module.split(".")).with_suffix(".py")


def _function_names(tree: ast.AST) -> set[str]:
    # Only top-level functions and class methods are tracked. Consumer sites
    # and behavioral anchors are documented as module-level or class-level;
    # a future entry pointing inside a nested helper would silently miss.
    names: set[str] = set()

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(f"{prefix}{node.name}")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body if isinstance(tree, ast.Module) else [])
    return names


def _site_resolves(site: str) -> bool:
    module, qualname = site.split(":", maxsplit=1)
    path = _module_path(module)
    return path.is_file() and qualname in _function_names(
        ast.parse(path.read_text(encoding="utf-8"))
    )


def _attribute_loads(modules: Iterable[str]) -> frozenset[str]:
    loads: set[str] = set()
    for module in modules:
        tree = ast.parse(_module_path(module).read_text(encoding="utf-8"), filename=module)
        loads.update(
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        )
    return frozenset(loads)


def _test_anchor_exists(anchor: str) -> bool:
    """Return True iff the named test function exists at the anchor path.

    This is a structural-only liveness check: it proves the named test
    function resolves at the documented path, but it does NOT prove the test
    exercises the specific declared field. Execution- and composition-
    classified fields share two category-level anchors
    (``_BEHAVIORAL_ANCHOR`` for execution, ``_COMPOSITION_ANCHOR`` for
    composition); the assertion that each declared field is *individually*
    exercised lives in the per-field behavioral coverage of the existing
    recipe/IO/validator test suites, not in this ledger. Per-field AST body
    scanning was considered and rejected because the existing shared anchors
    cover category-level liveness rather than per-field attribution, and
    tightening the check would require rewriting the registry to a
    per-field anchor layout.
    """
    path_text, function_name = anchor.split("::", maxsplit=1)
    path = _project_root() / path_text
    if not path.is_file():
        return False
    return function_name in _function_names(ast.parse(path.read_text(encoding="utf-8")))


def live_fields() -> set[FieldKey]:
    return {
        (owner, field.name)
        for owner in (RecipeIngredient, RecipeStep, Recipe)
        for field in dataclasses.fields(owner)
    }


def _field_label(key: FieldKey) -> str:
    return f"{key[0].__name__}.{key[1]}"


def _assert_registry_is_truthful(
    registry: dict[FieldKey, DeclaredFieldDef], *, attribute_loads: frozenset[str]
) -> None:
    unresolved = {
        _field_label(key): site
        for key, definition in registry.items()
        for site in definition.consumer_sites
        if not _site_resolves(site)
    }
    assert not unresolved, f"recipe dataclass consumer site(s) do not resolve: {unresolved}"
    missing_loads = sorted(_field_label(key) for key in registry if key[1] not in attribute_loads)
    assert not missing_loads, (
        "recipe dataclass fields classified with no attribute load in the finite "
        f"production scan surface: {missing_loads}"
    )
    missing_behavioral = {
        _field_label(key): definition.behavioral_test
        for key, definition in registry.items()
        if definition.classification in {"execution", "composition"}
        and (
            definition.behavioral_test is None
            or not _test_anchor_exists(definition.behavioral_test)
        )
    }
    assert not missing_behavioral, (
        f"runtime/composition fields need behavioral test anchors: {missing_behavioral}"
    )


def test_recipe_dataclass_fields_are_classified_or_deferred() -> None:
    fields = live_fields()
    classified = set(DECLARED_RECIPE_FIELDS)
    deferred = set(DEFERRED_RECIPE_FIELDS)
    assert not classified & deferred, "field classifications and deferrals must be disjoint"
    assert classified | deferred == fields, (
        "recipe dataclass ledger does not match live fields; "
        f"missing={sorted(map(_field_label, fields - classified - deferred))}, "
        f"stale={sorted(map(_field_label, (classified | deferred) - fields))}"
    )


def test_declared_recipe_field_consumers_are_real() -> None:
    _assert_registry_is_truthful(
        DECLARED_RECIPE_FIELDS, attribute_loads=_attribute_loads(_PRODUCTION_MODULES)
    )


def test_deferred_recipe_fields_are_current_and_explained() -> None:
    unconsumed = {key for key in live_fields() if key not in DECLARED_RECIPE_FIELDS}
    assert_entries_still_apply(
        DEFERRED_RECIPE_FIELDS,
        registry_name="DEFERRED_RECIPE_FIELDS",
        live_keys=unconsumed,
    )
    assert_not_stale(DEFERRED_RECIPE_FIELDS, registry_name="DEFERRED_RECIPE_FIELDS")
    assert_rationale_present(DEFERRED_RECIPE_FIELDS, registry_name="DEFERRED_RECIPE_FIELDS")


def test_fabricated_consumer_site_is_rejected() -> None:
    fabricated = {
        (RecipeStep, "skip_when_true"): DeclaredFieldDef(
            "execution", ("autoskillit.recipe.validator:does_not_exist",), _BEHAVIORAL_ANCHOR
        )
    }
    with pytest.raises(AssertionError, match="do not resolve"):
        _assert_registry_is_truthful(fabricated, attribute_loads=frozenset({"skip_when_true"}))


def test_pre_phase_one_skip_when_true_without_a_load_is_rejected() -> None:
    historic = {
        (RecipeStep, "skip_when_true"): DeclaredFieldDef(
            "execution", (_EXECUTION_SITE,), _BEHAVIORAL_ANCHOR
        )
    }
    with pytest.raises(AssertionError, match="no attribute load"):
        _assert_registry_is_truthful(historic, attribute_loads=frozenset())


def test_execution_field_without_behavioral_anchor_is_rejected() -> None:
    """Regression guard: the third assertion (behavioral test anchor) must fire when an
    execution-classified field points at a non-existent anchor test.

    The two sibling negative tests cover consumer-site and attribute-load gaps. Without
    this third negative case, a regression that silently drops the
    missing_behavioral block (lines 343-354) would not be caught by any existing
    negative test in this file.
    """
    unanchored = {
        (RecipeStep, "skip_when_true"): DeclaredFieldDef(
            "execution", (_EXECUTION_SITE,), "tests/does_not_exist.py::test_anchor"
        )
    }
    with pytest.raises(AssertionError, match="behavioral test anchors"):
        _assert_registry_is_truthful(unanchored, attribute_loads=frozenset({"skip_when_true"}))
