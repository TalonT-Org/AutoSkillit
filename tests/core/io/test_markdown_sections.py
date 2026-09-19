"""Tests for strict Markdown section primitives shared by authority domains."""

from __future__ import annotations

import pytest

from autoskillit.core import STEP_HEADING_RE, extract_section, split_table_row

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_extract_section_requires_one_matching_heading() -> None:
    markdown = "# Title\n\n## Allocation\nbody\n\n## Next\nmore\n"

    assert extract_section(markdown, "Allocation") == "\nbody\n\n"
    with pytest.raises(ValueError, match="exactly one"):
        extract_section(markdown, "Missing")
    with pytest.raises(ValueError, match="exactly one"):
        extract_section("## Allocation\na\n## Allocation\nb", "Allocation")


def test_pipe_row_and_step_pattern_are_strict() -> None:
    assert split_table_row("| one | two |") == ("one", "two")
    with pytest.raises(ValueError, match="pipe"):
        split_table_row("one | two")
    assert STEP_HEADING_RE.fullmatch("### Step 2.1: Title")
    assert STEP_HEADING_RE.fullmatch("### Step 3")
    assert not STEP_HEADING_RE.fullmatch("#### Step 3")
