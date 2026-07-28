"""Structural tests for the delivery registry and load-and-validate call sites.

AST guard: load_and_validate must only be called from _serve_helpers.py within server/tools/.
Registry membership: all four surfaces registered, no phantom entries.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "server" / "tools"


def _find_load_and_validate_call_files() -> set[str]:
    """Return basenames of files under server/tools/ that call load_and_validate."""
    offenders: set[str] = set()
    for py_file in _TOOLS_DIR.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match ctx.recipes.load_and_validate(...) or recipes.load_and_validate(...)
            if isinstance(func, ast.Attribute) and func.attr == "load_and_validate":
                offenders.add(py_file.name)
                break
    return offenders


def test_serve_surfaces_registry_is_exhaustive() -> None:
    """load_and_validate must only be called from _serve_helpers.py in server/tools/.

    tools_fleet_dispatch.py is exempt: its fleet-dispatch preflight call is Part B scope
    (issue #4208 Part B — fleet dispatch resolved_defaults gap). It is intentionally
    excluded from the Part A enforcement perimeter.
    TODO(#4208-B): remove tools_fleet_dispatch.py from the allowed set when Part B ships.
    """
    call_files = _find_load_and_validate_call_files()
    allowed = {"_serve_helpers.py", "tools_fleet_dispatch.py"}
    violations = call_files - allowed
    assert not violations, (
        f"load_and_validate called directly outside _serve_helpers.py in server/tools/: "
        f"{sorted(violations)}. "
        "All serve surfaces must call serve_recipe() from _serve_helpers.py instead."
    )


def test_recipe_delivery_surface_registry_is_typed() -> None:
    """The sole route authority is a typed core registry."""
    from autoskillit.core import (
        RECIPE_DELIVERY_SURFACE_REGISTRY,
        RecipeDeliverySurfaceDef,
    )

    assert isinstance(RECIPE_DELIVERY_SURFACE_REGISTRY, Mapping)
    assert all(
        isinstance(definition, RecipeDeliverySurfaceDef)
        for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
    )
    with pytest.raises(TypeError):
        operator.setitem(
            RECIPE_DELIVERY_SURFACE_REGISTRY,
            "mutated",
            next(iter(RECIPE_DELIVERY_SURFACE_REGISTRY.values())),
        )


def test_serve_surfaces_contains_expected_members() -> None:
    """The registry lists exactly the four recipe-bearing serve routes."""
    from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY

    expected = {
        "open_kitchen",
        "open_kitchen_deferred_recall",
        "load_recipe",
        "get_recipe",
    }
    actual = set(RECIPE_DELIVERY_SURFACE_REGISTRY)
    assert expected == actual, (
        f"RECIPE_DELIVERY_SURFACE_REGISTRY mismatch. "
        f"Missing: {expected - actual}. "
        f"Extra: {actual - expected}."
    )


def test_recipe_recreation_policy_is_registry_owned() -> None:
    from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY

    eligible = {
        surface
        for surface, definition in RECIPE_DELIVERY_SURFACE_REGISTRY.items()
        if definition.recreation_eligible
    }

    assert eligible == {"open_kitchen", "open_kitchen_deferred_recall", "get_recipe"}


def test_recipe_initialization_activation_is_registry_owned() -> None:
    from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY

    assert {
        surface
        for surface, definition in RECIPE_DELIVERY_SURFACE_REGISTRY.items()
        if definition.initialization_activating
    } == {"open_kitchen", "open_kitchen_deferred_recall"}
    assert all(
        type(definition.initialization_activating) is bool
        for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
    )
