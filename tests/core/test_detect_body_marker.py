"""Tests for centralized issue-body marker detection."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from autoskillit.core import (
    INVESTIGATION_COMPLETE_MARKER,
    REVIEW_APPROACH_MARKER,
    detect_body_marker,
    strip_markdown_code_regions,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_detect_body_marker_genuine_marker() -> None:
    body = f"## Investigation\n\n{INVESTIGATION_COMPLETE_MARKER}"
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is True


def test_detect_body_marker_marker_in_fence() -> None:
    body = f"```\n{INVESTIGATION_COMPLETE_MARKER}\n```"
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is False


def test_detect_body_marker_marker_in_inline_span() -> None:
    body = f"`{INVESTIGATION_COMPLETE_MARKER}`"
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is False


def test_detect_body_marker_mixed_genuine_and_quoted() -> None:
    body = (
        f"## Investigation\n\n{INVESTIGATION_COMPLETE_MARKER}\n\n"
        f"```\n{INVESTIGATION_COMPLETE_MARKER}\n```"
    )
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is True


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (f"## Review Approach\n\n{REVIEW_APPROACH_MARKER}", True),
        (f"~~~\n{REVIEW_APPROACH_MARKER}\n~~~", False),
        (f"`{REVIEW_APPROACH_MARKER}`", False),
    ],
)
def test_detect_body_marker_review_approach(body: str, expected: bool) -> None:
    assert detect_body_marker(body, REVIEW_APPROACH_MARKER) is expected


def test_detect_body_marker_empty_body() -> None:
    assert detect_body_marker("", INVESTIGATION_COMPLETE_MARKER) is False


def test_detect_body_marker_no_marker() -> None:
    assert detect_body_marker("Just a regular issue body.", INVESTIGATION_COMPLETE_MARKER) is False


def test_detect_body_marker_marker_in_tilde_fence() -> None:
    body = f"~~~\n{INVESTIGATION_COMPLETE_MARKER}\n~~~"
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is False


def test_detect_body_marker_survives_stray_backtick_on_other_line() -> None:
    body = (
        f"Use the `--foo flag (typo).\n\n{INVESTIGATION_COMPLETE_MARKER}\n\nAlso see `--bar` here."
    )
    assert detect_body_marker(body, INVESTIGATION_COMPLETE_MARKER) is True


@given(st.text())
def test_strip_markdown_code_regions_is_idempotent(text: str) -> None:
    stripped = strip_markdown_code_regions(text)
    assert strip_markdown_code_regions(stripped) == stripped


@given(st.text(), st.text(min_size=1))
def test_detect_body_marker_stripped_occurrences_never_exceed_raw(
    body: str,
    marker: str,
) -> None:
    assert strip_markdown_code_regions(body).count(marker) <= body.count(marker)
