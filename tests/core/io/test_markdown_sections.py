"""Tests for strict Markdown section primitives shared by authority domains."""

from __future__ import annotations

import pytest

from autoskillit.core import STEP_HEADING_RE, extract_section, parse_pipe_table, split_table_row

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_extract_section_requires_one_matching_heading() -> None:
    markdown = "# Title\n\n## Allocation\nbody\n\n## Next\nmore\n"

    assert extract_section(markdown, "Allocation") == "\nbody\n\n"
    with pytest.raises(ValueError, match="exactly one") as missing:
        extract_section(markdown, "Missing")
    assert str(missing.value).startswith("expected exactly one ## Missing section")
    assert "; found 0 matching headings" in str(missing.value)
    with pytest.raises(ValueError, match="exactly one") as duplicate:
        extract_section("## Allocation\na\n## Allocation\nb", "Allocation")
    assert str(duplicate.value).startswith("expected exactly one ## Allocation section")
    assert "; found 2 matching headings" in str(duplicate.value)


@pytest.mark.parametrize(
    ("section", "offending_line", "message_part"),
    (
        pytest.param(
            "| Wrong | Allocation | Implementation Step |\n| --- | --- | --- |",
            "| Wrong | Allocation | Implementation Step |",
            "header",
            id="wrong-header",
        ),
        pytest.param(
            "| Requirement ID | Allocation | Implementation Step |\n| -- | --- | --- |",
            "| -- | --- | --- |",
            "separator",
            id="bad-separator",
        ),
        pytest.param(
            "| Requirement ID | Allocation | Implementation Step |\n"
            "| --- | --- | --- |\nnot a pipe row",
            "not a pipe row",
            "pipe",
            id="non-pipe-line",
        ),
        pytest.param(
            "| Requirement ID | Allocation | Implementation Step |\n"
            "| --- | --- | --- |\n| R1 | owned | Step 1.1 | extra |",
            "| R1 | owned | Step 1.1 | extra |",
            "3 columns",
            id="four-cell-row",
        ),
        pytest.param(
            f"| {'x' * 150} | Allocation | Implementation Step |\n| --- | --- | --- |",
            f"| {'x' * 150} | Allocation | Implementation Step |",
            "header",
            id="long-line-is-truncated",
        ),
    ),
)
def test_parse_pipe_table_errors_quote_stripped_bounded_line(
    section: str, offending_line: str, message_part: str
) -> None:
    with pytest.raises(ValueError) as error:
        parse_pipe_table(
            section,
            ("Requirement ID", "Allocation", "Implementation Step"),
        )

    message = str(error.value)
    stripped = offending_line.strip()
    assert message_part in message.lower()
    assert stripped[:120] in message
    if len(stripped) > 120:
        assert stripped[120:] not in message


def test_pipe_row_and_step_pattern_are_strict() -> None:
    assert split_table_row("| one | two |") == ("one", "two")
    with pytest.raises(ValueError, match="pipe"):
        split_table_row("one | two")
    assert STEP_HEADING_RE.fullmatch("### Step 2.1: Title")
    assert STEP_HEADING_RE.fullmatch("### Step 3")
    assert not STEP_HEADING_RE.fullmatch("#### Step 3")
