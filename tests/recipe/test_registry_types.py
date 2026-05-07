"""Tests for RuleDef and BlockRuleDef registry types."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.recipe.registry import BlockRuleDef, RuleDef

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_rule_def_is_frozen() -> None:
    assert dataclasses.fields(RuleDef)
    assert RuleDef.__dataclass_params__.frozen is True


def test_block_rule_def_is_frozen() -> None:
    assert dataclasses.fields(BlockRuleDef)
    assert BlockRuleDef.__dataclass_params__.frozen is True
