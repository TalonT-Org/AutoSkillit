"""Tests for _skill_placeholder_parser validation rule block extractor."""

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_blockquote_placeholders,
    extract_blockquote_sections,
    extract_validation_rule_block,
)

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


# --- extract_blockquote_sections ---


def test_extract_blockquote_sections_identifies_contiguous_blocks() -> None:
    """A 3+ line contiguous blockquote run is yielded with the nearest step heading."""
    sample = (
        "### Step 3: Dispatch subagent\n"
        "\n"
        "> Review the diff.\n"
        "> Report findings.\n"
        "> Be concise.\n"
        "\n"
        "Trailing prose.\n"
    )
    blocks = extract_blockquote_sections(sample)
    assert len(blocks) == 1
    heading, text = blocks[0]
    assert heading == "Step 3"
    assert "Review the diff." in text
    assert "Report findings." in text
    assert "Be concise." in text


def test_extract_blockquote_sections_excludes_single_line_callouts() -> None:
    """Single-line `>` callouts without content signals are stylistic, not prompts."""
    sample = "### Step 1: Note\n\n> **Note:** This is a stylistic callout.\n\nBody prose.\n"
    blocks = extract_blockquote_sections(sample)
    assert blocks == []


def test_extract_blockquote_sections_includes_two_line_prompt_with_banned_var() -> None:
    """A 2-line blockquote containing a {*_content} placeholder IS returned (content signal)."""
    sample = (
        "### Step 2: Inline content\n\n> Review {annotated_diff_content}.\n> Report findings.\n"
    )
    blocks = extract_blockquote_sections(sample)
    assert len(blocks) == 1
    heading, text = blocks[0]
    assert heading == "Step 2"
    assert "{annotated_diff_content}" in text


def test_extract_blockquote_sections_strips_prefix() -> None:
    """Returned text has the `> ` prefix stripped from each line."""
    sample = "### Step 4\n\n> First line.\n> Second line.\n> Third line.\n"
    blocks = extract_blockquote_sections(sample)
    assert len(blocks) == 1
    _, text = blocks[0]
    for line in text.splitlines():
        assert not line.startswith(">"), f"Prefix not stripped: {line!r}"


def test_extract_blockquote_sections_trailing_block_at_eof() -> None:
    """A blockquote that runs to end of file is flushed, not silently dropped."""
    sample = "### Step 5\n\n> Line one.\n> Line two.\n> Line three."
    blocks = extract_blockquote_sections(sample)
    assert len(blocks) == 1
    heading, text = blocks[0]
    assert heading == "Step 5"
    assert "Line one." in text
    assert "Line two." in text
    assert "Line three." in text


def test_extract_blockquote_sections_tuple_order_heading_first() -> None:
    """First tuple element is the step heading, second is the body text."""
    sample = "### Step 7\n\n> First.\n> Second.\n> Third.\n"
    blocks = extract_blockquote_sections(sample)
    assert len(blocks) == 1
    heading, body = blocks[0]
    assert heading == "Step 7"
    assert isinstance(heading, str)
    assert isinstance(body, str)
    assert "First." in body


# --- extract_blockquote_placeholders ---


def test_extract_blockquote_placeholders_extracts_content_suffix_vars() -> None:
    """Given a blockquote with {annotated_diff_content} and {diff_content}, return both names."""
    text = "Review the following:\n{annotated_diff_content}\nand {diff_content} here.\n"
    placeholders = extract_blockquote_placeholders(text)
    assert placeholders == {"annotated_diff_content", "diff_content"}


def test_extract_blockquote_placeholders_ignores_non_content_vars() -> None:
    """A blockquote with only {*_path} vars (no {*_content}) returns an empty set."""
    text = "Read {annotated_diff_path}.\nInspect {diff_path}.\n"
    placeholders = extract_blockquote_placeholders(text)
    assert placeholders == set()
