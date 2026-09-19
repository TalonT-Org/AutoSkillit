"""Pinned grammar tests for deterministic plan-set requirement inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    InventoryMode,
    RequirementKind,
    compute_bytes_hash,
    extract_requirement_inventory,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plan_set"


def test_inline_children_keep_raw_span_hashes() -> None:
    markdown = "## Requirements\n1. **Preamble** (1a) `first` item\n   continuation\n"

    extraction = extract_requirement_inventory(markdown, issue_number=1)

    assert extraction.mode is InventoryMode.ENUMERATED
    parent, child = extraction.requirements
    assert parent.kind is RequirementKind.CONTAINER
    assert child.parent_label == "1"
    assert child.text == "first item continuation"
    assert child.text_digest == compute_bytes_hash(
        b"1. **Preamble** (1a) `first` item\n   continuation\n"
    )


def test_historical_fixture_and_unsupported_bullet_are_explicit() -> None:
    historical = (_FIXTURES / "issue_4253" / "issue.md").read_text(encoding="utf-8")

    extraction = extract_requirement_inventory(historical, issue_number=4253)

    assert {item.requirement_id for item in extraction.requirements} == {
        "1",
        "1a",
        "1b",
        "1c",
        "2",
        "3",
    }
    assert (
        next(item for item in extraction.requirements if item.requirement_id == "1").kind
        is RequirementKind.CONTAINER
    )
    malformed = extract_requirement_inventory("## Requirements\n- prose bullet\n", issue_number=1)
    assert malformed.unparsed_marker_lines == (2,)
