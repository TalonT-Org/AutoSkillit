"""Tests for _skill_placeholder_parser validation rule block extractor."""

import pytest

from autoskillit.recipe._skill_placeholder_parser import extract_validation_rule_block

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_extract_validation_rule_block_returns_v9() -> None:
    """extract_validation_rule_block must extract a named V-rule block from SKILL.md."""
    sample = (
        "V8: success criteria cross-reference\n"
        "  WARNING if conclusive_positive does not reference a metric.\n\n"
        "V9: data_manifest completeness\n"
        "  ERROR if source_type: external lacks acquisition.\n"
        "  ERROR if acquisition contains {placeholder} tokens.\n\n"
        "---\n"
    )
    block = extract_validation_rule_block(sample, "V9")
    assert block is not None
    assert "data_manifest" in block
    assert "{placeholder}" in block
    assert "V8" not in block


def test_extract_validation_rule_block_returns_none_for_missing() -> None:
    """extract_validation_rule_block returns None for a missing rule."""
    block = extract_validation_rule_block("V1: some rule\n", "V9")
    assert block is None


def test_extract_validation_rule_block_returns_first_v() -> None:
    """Extract the first V-rule when requested."""
    sample = "V1: first rule\n  ERROR on fail.\n\nV2: second rule\n  WARN on warn.\n"
    block = extract_validation_rule_block(sample, "V1")
    assert block is not None
    assert "first rule" in block
    assert "V2" not in block


def test_extract_validation_rule_block_stops_at_separator() -> None:
    """Block extraction stops at --- separator."""
    sample = "V1: rule body\n\n---\n\nOther content that is not part of V1\n"
    block = extract_validation_rule_block(sample, "V1")
    assert block is not None
    assert "Other content" not in block


def test_extract_validation_rule_block_different_rule() -> None:
    """Can extract V2 as well as V1."""
    sample = "V1: first rule\nV2: second rule\nV3: third rule\n"
    block = extract_validation_rule_block(sample, "V2")
    assert block is not None
    assert "V1" not in block
    assert "V2" in block
    assert "V3" not in block
