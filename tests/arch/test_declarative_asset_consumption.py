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

_UNREAD_LEAF_PATHS = (
    "families.arch-lens.activate_deps",
    "families.arch-lens.arg_interface",
    "families.arch-lens.default_enabled",
    "families.arch-lens.description",
    "families.arch-lens.dial_skill",
    "families.arch-lens.failure_mode",
    "families.arch-lens.lens_count",
    "families.arch-lens.mode_label",
    "families.arch-lens.output_prefix",
    "families.arch-lens.output_type",
    "families.arch-lens.status",
    "families.arch-lens.synthesis.strategy",
    "families.exp-lens.activate_deps",
    "families.exp-lens.arg_interface",
    "families.exp-lens.default_enabled",
    "families.exp-lens.description",
    "families.exp-lens.dial_skill",
    "families.exp-lens.failure_mode",
    "families.exp-lens.lens_count",
    "families.exp-lens.mode_label",
    "families.exp-lens.output_prefix",
    "families.exp-lens.output_type",
    "families.exp-lens.status",
    "families.exp-lens.synthesis.strategy",
    "families.refactor-lens.arg_interface",
    "families.refactor-lens.default_enabled",
    "families.refactor-lens.description",
    "families.refactor-lens.dial_skill",
    "families.refactor-lens.failure_mode",
    "families.refactor-lens.lens_count",
    "families.refactor-lens.mode_label",
    "families.refactor-lens.output_type",
    "families.refactor-lens.status",
    "families.refactor-lens.synthesis.strategy",
    "families.vis-lens.activate_deps",
    "families.vis-lens.arg_interface",
    "families.vis-lens.composite_slugs",
    "families.vis-lens.default_enabled",
    "families.vis-lens.description",
    "families.vis-lens.dial_skill",
    "families.vis-lens.failure_mode",
    "families.vis-lens.lens_count",
    "families.vis-lens.lens_metadata.methodology-norms.special_assertions",
    "families.vis-lens.mode_label",
    "families.vis-lens.output_prefix",
    "families.vis-lens.output_type",
    "families.vis-lens.phase_skip.applies_to",
    "families.vis-lens.phase_skip.skip_field",
    "families.vis-lens.phase_skip.skip_semantics",
    "families.vis-lens.status",
    "families.vis-lens.synthesis.skill",
    "families.vis-lens.synthesis.strategy",
)

PHOROPTER_LEAF_DEFERRALS = {
    path: TrackedDeferral(
        issue=4894,
        rationale="This declarative phoropter leaf has no production semantic reader yet.",
        added_date=date(2026, 8, 27),
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


def test_unread_leaf_deferrals_are_current_and_explained() -> None:
    data = load_yaml(_REGISTRY_PATH)
    unread = _leaf_paths(data["families"], "families") - set(PRODUCTION_LEAF_READERS)
    assert_entries_still_apply(
        PHOROPTER_LEAF_DEFERRALS,
        registry_name="PHOROPTER_LEAF_DEFERRALS",
        live_keys=unread,
    )
    assert_not_stale(PHOROPTER_LEAF_DEFERRALS, registry_name="PHOROPTER_LEAF_DEFERRALS")
    assert_rationale_present(PHOROPTER_LEAF_DEFERRALS, registry_name="PHOROPTER_LEAF_DEFERRALS")
