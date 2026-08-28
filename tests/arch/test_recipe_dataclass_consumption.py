"""Owner-qualified recipe-dataclass consumption ledger.

This is deliberately a finite AST scan, not whole-program type inference.  It
proves a declared source surface contains an attribute load and that the named
consumer function resolves, but it cannot prove control-flow reachability or
the runtime owner of a syntactically identical attribute access.  Keeping the
ledger owner-qualified, limiting the source surface, and requiring behavioral
test anchors for runtime/composition fields make that limitation explicit.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

RecipeDataclass = type[Recipe] | type[RecipeIngredient] | type[RecipeStep]
FieldKey = tuple[RecipeDataclass, str]
_Classification = Literal["execution", "composition", "validation-only"]


@dataclasses.dataclass(frozen=True, slots=True)
class DeclaredFieldDef:
    classification: _Classification
    consumer_sites: tuple[str, ...]
    behavioral_test: str | None = None


_BEHAVIORAL_ANCHOR = (
    "tests/server/test_step_guard_admission.py::"
    "test_truthy_guard_bypasses_dispatch_marks_tracker_and_unblocks_dependents"
)
_COMPOSITION_ANCHOR = (
    "tests/recipe/test_skip_guard_deferral.py::"
    "test_resolve_skip_guards_strips_optional_true_on_truthy"
)
_VALIDATION_SITE = "autoskillit.recipe.validator:validate_recipe_structure"
_COMPOSITION_SITE = "autoskillit.recipe._recipe_composition:_build_active_recipe"
_EXECUTION_SITE = "autoskillit.server._recipe_execution:build_recipe_execution_snapshot"
_RECIPE_SITE = "autoskillit.recipe.io:load_recipe"


def _field_defs(
    owner: RecipeDataclass,
    fields: Iterable[str],
    classification: _Classification,
    consumer_site: str,
    behavioral_test: str | None = None,
) -> dict[FieldKey, DeclaredFieldDef]:
    return {
        (owner, field): DeclaredFieldDef(classification, (consumer_site,), behavioral_test)
        for field in fields
    }


DECLARED_RECIPE_FIELDS: dict[FieldKey, DeclaredFieldDef] = (
    _field_defs(
        RecipeIngredient,
        ("default", "description", "hidden", "required"),
        "execution",
        _EXECUTION_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "action",
            "idle_output_timeout",
            "message",
            "model",
            "provider",
            "stale_threshold",
            "tool",
            "with_args",
            "skip_when_true",
        ),
        "execution",
        _EXECUTION_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "gate",
            "on_context_limit",
            "on_exhausted",
            "on_failure",
            "on_rate_limit",
            "on_result",
            "on_skip",
            "on_success",
            "skip_when_false",
            "sub_recipe",
        ),
        "composition",
        _COMPOSITION_SITE,
        _COMPOSITION_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "block",
            "capture",
            "capture_list",
            "constant",
            "name",
            "note",
            "optional",
            "optional_context_refs",
            "pass_through",
            "phoropter_family",
            "python",
            "retries",
        ),
        "validation-only",
        _VALIDATION_SITE,
    )
    | _field_defs(
        Recipe,
        (
            "categories",
            "composite_hash",
            "content_hash",
            "description",
            "dispatch_only",
            "experimental",
            "ingredients",
            "kitchen_rules",
            "name",
            "requires_features",
            "requires_packs",
            "summary",
        ),
        "execution",
        _RECIPE_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        Recipe,
        (
            "delivery_segments",
            "dispatches",
            "kind",
            "steps",
        ),
        "composition",
        _COMPOSITION_SITE,
        _COMPOSITION_ANCHOR,
    )
    | _field_defs(
        Recipe,
        ("recipe_version", "version"),
        "validation-only",
        _VALIDATION_SITE,
    )
)

# These fields are deliberately declared but have no effective consumer.  The
# issue bodies describe the missing authority/type/dispatch semantics; do not
# turn a deferral into a classification merely because a parser happens to see it.
DEFERRED_RECIPE_FIELDS: dict[FieldKey, TrackedDeferral] = {
    (RecipeIngredient, "authority"): TrackedDeferral(
        issue=4891,
        rationale="Kitchen opening accepts config authority but leaves its no-op path unenforced.",
        added_date=date(2026, 8, 27),
    ),
    (RecipeIngredient, "type"): TrackedDeferral(
        issue=4892,
        rationale="Declared ingredient types have no value-level validation at input resolution.",
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "allowed_recipes"): TrackedDeferral(
        issue=4893,
        rationale="Recipe allowlists are parsed but not enforced by recipe dispatch.",
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "blocks"): TrackedDeferral(
        issue=4893,
        rationale=(
            "Parsed recipe blocks are assigned but have no external validation or runtime reader."
        ),
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "continue_on_failure"): TrackedDeferral(
        issue=4893,
        rationale=(
            "The recipe failure-continuation declaration has no composition or execution consumer."
        ),
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "requires_recipe_packs"): TrackedDeferral(
        issue=4893,
        rationale="Required recipe packs are parsed but not enforced by recipe dispatch.",
        added_date=date(2026, 8, 27),
    ),
    (RecipeStep, "declared_with_args"): TrackedDeferral(
        issue=4893,
        rationale=(
            "The declared argument snapshot is self-validated but has no external "
            "behavior consumer."
        ),
        added_date=date(2026, 8, 27),
    ),
}

DECLARED_RECIPE_FIELDS = dict(
    sorted(
        DECLARED_RECIPE_FIELDS.items(),
        key=lambda item: (item[0][0].__name__, item[0][1]),
    )
)
DEFERRED_RECIPE_FIELDS = dict(
    sorted(
        DEFERRED_RECIPE_FIELDS.items(),
        key=lambda item: (item[0][0].__name__, item[0][1]),
    )
)

_PRODUCTION_MODULES = (
    "autoskillit.recipe._analysis",
    "autoskillit.recipe._api_orchestration",
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
