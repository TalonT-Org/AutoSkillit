"""Leaf-level consumption ledger for the phoropter family registry."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import load_yaml, pkg_root
from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
_PREFIX_READER = "autoskillit.recipe.rules.rules_phoropter_adjacency:_load_registry_yaml"
PRODUCTION_LEAF_READERS = {
    f"families.{family}.step_naming.prefix": _PREFIX_READER
    for family in ("arch-lens", "exp-lens", "vis-lens", "refactor-lens")
}

# Post-#4894: the phoropter-registry contains only ``step_naming.prefix`` per
# family — every other historically-unread leaf was retired (see #4894 for the
# tracking issue and ``tests/contracts/test_phoropter_registry_leaf_has_consumer.py``
# for the re-accretion guard). The deferral ledger remains in place as a forward
# mechanism for any future declarative phoropter leaf that needs to be tracked
# before its reader lands.
_UNREAD_LEAF_PATHS: tuple[str, ...] = ()

PHOROPTER_LEAF_DEFERRALS: dict[str, TrackedDeferral] = {
    path: TrackedDeferral(
        issue=4894,
        rationale="This declarative phoropter leaf has no production semantic reader yet.",
        added_date=date(2026, 8, 27),
        regression_test=(
            "tests/arch/test_declarative_asset_consumption.py::"
            "test_unread_leaf_deferrals_are_current_and_explained"
        ),
    )
    for path in _UNREAD_LEAF_PATHS
}


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        return {
            path
            for key, child in value.items()
            for path in _leaf_paths(child, f"{prefix}.{key}" if prefix else str(key))
        }
    return {prefix}


def _function_exists(site: str) -> bool:
    module, function = site.split(":", maxsplit=1)
    path = (
        Path(__file__).resolve().parents[2] / "src" / Path(*module.split(".")).with_suffix(".py")
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        for node in ast.walk(tree)
    )


def test_every_family_leaf_has_a_reader_or_tracked_deferral() -> None:
    data = load_yaml(_REGISTRY_PATH)
    live_leaves = _leaf_paths(data["families"], "families")
    tracked = set(PRODUCTION_LEAF_READERS) | set(PHOROPTER_LEAF_DEFERRALS)
    assert tracked == live_leaves, (
        "phoropter-registry leaf coverage drift; "
        f"missing={sorted(live_leaves - tracked)}, stale={sorted(tracked - live_leaves)}"
    )


def test_prefix_reader_is_real_and_leaf_specific() -> None:
    assert set(PRODUCTION_LEAF_READERS.values()) == {_PREFIX_READER}
    assert _function_exists(_PREFIX_READER)


def test_unread_leaf_deferrals_are_current_and_explained(
    request: pytest.FixtureRequest,
) -> None:
    data = load_yaml(_REGISTRY_PATH)
    unread = _leaf_paths(data["families"], "families") - set(PRODUCTION_LEAF_READERS)
    assert_entries_still_apply(
        PHOROPTER_LEAF_DEFERRALS,
        registry_name="PHOROPTER_LEAF_DEFERRALS",
        live_keys=unread,
    )
    assert_not_stale(PHOROPTER_LEAF_DEFERRALS, registry_name="PHOROPTER_LEAF_DEFERRALS")
    assert_rationale_present(PHOROPTER_LEAF_DEFERRALS, registry_name="PHOROPTER_LEAF_DEFERRALS")
    assert_deferrals_have_regression_tests(
        PHOROPTER_LEAF_DEFERRALS,
        registry_name="PHOROPTER_LEAF_DEFERRALS",
        collected_node_ids={item.nodeid for item in request.session.items},
    )
