"""Allocation parsing is strict and step evidence is local to one part."""

from __future__ import annotations

import pytest

from autoskillit.core import PlanSetRejectReason, parse_part_allocation, verify_allocation_evidence

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_allocation_requires_step_that_mentions_requirement() -> None:
    markdown = """### Step 1.1: implementation

The step omits the identifier.

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
| R1 | owned | Step 1.1 |
"""
    rows = parse_part_allocation(markdown, part_key="P1")

    assert verify_allocation_evidence(markdown, rows) == (
        ("R1", PlanSetRejectReason.ALLOCATION_STEP_UNREFERENCED),
    )
    with pytest.raises(ValueError, match="exactly one"):
        parse_part_allocation("# no allocation", part_key="P1")


def test_allocation_flags_step_not_present_in_markdown() -> None:
    markdown = """### Step 1.1: implementation

The step exists.

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
| R1 | owned | Step 99.9 |
"""
    rows = parse_part_allocation(markdown, part_key="P1")

    assert verify_allocation_evidence(markdown, rows) == (
        ("R1", PlanSetRejectReason.ALLOCATION_STEP_MISSING),
    )
