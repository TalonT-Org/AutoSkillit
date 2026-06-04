"""Tests for RuleDef and BlockRuleDef registry types."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import BlockRuleDef, RuleDef

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_rule_def_is_frozen() -> None:
    assert dataclasses.is_dataclass(RuleDef)
    assert RuleDef.__dataclass_params__.frozen is True


def test_block_rule_def_is_frozen() -> None:
    assert dataclasses.is_dataclass(BlockRuleDef)
    assert BlockRuleDef.__dataclass_params__.frozen is True


def test_rule_def_mutation_raises() -> None:
    instance = RuleDef(
        name="test-rule",
        description="A test rule",
        severity=Severity.WARNING,
        check=lambda ctx: [],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.name = "mutated"  # type: ignore[misc]


def test_block_rule_def_mutation_raises() -> None:
    instance = BlockRuleDef(
        name="test-block-rule",
        description="A test block rule",
        severity=Severity.WARNING,
        check=lambda ctx: [],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.name = "mutated"  # type: ignore[misc]
