"""Contracts for the schema-driven recipe-section registry."""

from __future__ import annotations

import dataclasses
import operator
import typing
from collections.abc import Mapping

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_FIXED_SECTION_NAMES = {
    "content",
    "ingredients_table",
    "orchestration_rules",
    "stop_step_semantics",
    "errors",
    "warnings",
}


def test_recipe_section_registry_has_exact_fixed_and_separate_dynamic_definitions() -> None:
    from autoskillit.core import (
        DYNAMIC_RECIPE_SECTION_DEF,
        RECIPE_SECTION_REGISTRY,
        RecipeSectionDef,
    )

    assert isinstance(RECIPE_SECTION_REGISTRY, Mapping)
    assert set(RECIPE_SECTION_REGISTRY) == _FIXED_SECTION_NAMES
    assert all(
        isinstance(definition, RecipeSectionDef) for definition in RECIPE_SECTION_REGISTRY.values()
    )
    assert isinstance(DYNAMIC_RECIPE_SECTION_DEF, RecipeSectionDef)
    assert DYNAMIC_RECIPE_SECTION_DEF.name not in RECIPE_SECTION_REGISTRY
    assert DYNAMIC_RECIPE_SECTION_DEF.section_strategy == "raw"
    assert DYNAMIC_RECIPE_SECTION_DEF.ordinary_content_format == "raw-text"


def test_recipe_section_definition_and_registry_are_immutable() -> None:
    from autoskillit.core import RECIPE_SECTION_REGISTRY, RecipeSectionDef

    definition = RECIPE_SECTION_REGISTRY["content"]
    assert dataclasses.is_dataclass(RecipeSectionDef)
    assert RecipeSectionDef.__dataclass_params__.frozen is True
    assert "__slots__" in RecipeSectionDef.__dict__

    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        operator.setitem(RECIPE_SECTION_REGISTRY, "changed", definition)


@pytest.mark.parametrize(
    ("section", "value_kind", "element_kind", "strategy", "ordinary", "oversized"),
    [
        ("content", "string", None, "raw", "raw-text", None),
        ("ingredients_table", "string", None, "scalar", "json-scalar-page", None),
        ("orchestration_rules", "string", None, "raw", "raw-text", None),
        ("stop_step_semantics", "string", None, "raw", "raw-text", None),
        (
            "errors",
            "array",
            "string",
            "array",
            "json-array-page",
            "json-element-fragment",
        ),
        (
            "warnings",
            "array",
            "string",
            "array",
            "json-array-page",
            "json-element-fragment",
        ),
    ],
)
def test_recipe_section_strategy_and_format_combinations_are_pinned(
    section: str,
    value_kind: str,
    element_kind: str | None,
    strategy: str,
    ordinary: str,
    oversized: str | None,
) -> None:
    from autoskillit.core import RECIPE_SECTION_REGISTRY

    definition = RECIPE_SECTION_REGISTRY[section]
    assert (
        definition.value_kind,
        definition.element_kind,
        definition.section_strategy,
        definition.ordinary_content_format,
        definition.oversized_content_format,
    ) == (value_kind, element_kind, strategy, ordinary, oversized)


def test_recipe_section_presence_and_default_semantics_are_explicit() -> None:
    from autoskillit.core import RECIPE_SECTION_REGISTRY

    ingredients = RECIPE_SECTION_REGISTRY["ingredients_table"]
    assert ingredients.missing_behavior == "absent"
    assert ingredients.none_behavior == "absent"
    assert ingredients.has_default is False

    for section in ("errors", "warnings"):
        definition = RECIPE_SECTION_REGISTRY[section]
        assert definition.missing_behavior == "default"
        assert definition.none_behavior == "invalid"
        assert definition.has_default is True
        assert definition.default_value == ()


def test_recipe_section_registry_identity_is_stable_and_qualified() -> None:
    from autoskillit.core import (
        RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        RECIPE_SECTION_PAGINATION_VERSION,
        RECIPE_SECTION_REGISTRY_DIGEST,
    )

    assert RECIPE_SECTION_PAGINATION_VERSION == 1
    for digest in (
        RECIPE_SECTION_REGISTRY_DIGEST,
        RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
    ):
        algorithm, separator, hexadecimal = digest.partition(":")
        assert (algorithm, separator, len(hexadecimal)) == ("sha256", ":", 64)
        int(hexadecimal, 16)
    assert RECIPE_SECTION_REGISTRY_DIGEST != RECIPE_SECTION_PAGINATION_POLICY_DIGEST


def test_pullable_sections_match_public_result_schemas() -> None:
    from autoskillit.core import RECIPE_SECTION_REGISTRY
    from autoskillit.recipe import LoadRecipeResult, OpenKitchenResult

    load_hints = typing.get_type_hints(LoadRecipeResult)
    open_hints = typing.get_type_hints(OpenKitchenResult)

    assert _FIXED_SECTION_NAMES <= load_hints.keys()
    assert _FIXED_SECTION_NAMES <= open_hints.keys()
    for hints in (load_hints, open_hints):
        assert hints["content"] is str
        assert hints["ingredients_table"] == str | None
        assert hints["orchestration_rules"] is str
        assert hints["stop_step_semantics"] is str
        assert hints["errors"] == list[str]
        assert hints["warnings"] == list[str]
        assert hints["post_prune_step_names"] == list[str]

    assert set(RECIPE_SECTION_REGISTRY) == _FIXED_SECTION_NAMES


def test_recipe_section_contract_is_exported_through_both_core_gateways() -> None:
    import autoskillit.core as core
    import autoskillit.core.types as core_types

    expected_exports = {
        "DYNAMIC_RECIPE_SECTION_DEF",
        "RECIPE_SECTION_MANDATORY_FAILURE_CODES",
        "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
        "RECIPE_SECTION_PAGINATION_VERSION",
        "RECIPE_SECTION_REGISTRY",
        "RECIPE_SECTION_REGISTRY_DIGEST",
        "RECIPE_SECTION_RESPONSE_FLOOR_BYTES",
        "RecipeSectionDef",
        "RecipeSectionValidationFinding",
        "canonical_recipe_section_json",
        "recipe_section_digest",
        "recipe_section_element_digest",
        "recipe_section_plan_digest",
        "validate_recipe_artifact_sections",
    }

    for name in expected_exports:
        assert getattr(core_types, name) is getattr(core, name)
