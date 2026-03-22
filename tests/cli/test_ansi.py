"""Tests for cli/_ansi.py terminal color utilities."""

from __future__ import annotations

import re

import pytest

from autoskillit.cli._ansi import ingredients_to_terminal, supports_color


def test_supports_color_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR env var disables color."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert not supports_color()


def test_supports_color_respects_dumb_term(monkeypatch: pytest.MonkeyPatch) -> None:
    """TERM=dumb disables color."""
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not supports_color()


def test_supports_color_false_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY stdout returns False."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    # In test runners stdout is typically not a tty
    assert not supports_color()


def test_ingredients_to_terminal_bounded_width_with_long_description():
    """ingredients_to_terminal must never produce a line wider than 120 chars,
    even when a description is 220+ characters (as in implementation.yaml run_mode)."""
    long_desc = "Execution mode when processing multiple issues. " * 5  # 240+ chars
    rows = [
        ("task", long_desc, "(required)"),
        ("run_mode", long_desc, "sequential"),
    ]
    result = ingredients_to_terminal(rows)
    for line in result.splitlines():
        # strip ANSI escape codes before measuring
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert len(plain) <= 120, f"Line too wide ({len(plain)} chars): {plain!r}"


def test_ingredients_to_terminal_truncates_with_ellipsis():
    """Descriptions longer than the max column width must be truncated with '…'."""
    long_desc = "A" * 100
    rows = [("param", long_desc, "default")]
    result = ingredients_to_terminal(rows)
    assert "…" in result
    assert "A" * 100 not in result  # full string must not appear


def test_ingredients_to_terminal_short_description_not_truncated():
    """Short descriptions must be displayed verbatim without truncation."""
    rows = [("task", "What to do", "(required)")]
    result = ingredients_to_terminal(rows)
    assert "What to do" in result
    assert "…" not in result


def test_ingredients_to_terminal_columns_aligned():
    """All data rows must have the same column positions — alignment holds after truncation."""
    rows = [
        ("task", "Short", "(required)"),
        ("run_mode", "X" * 200, "sequential"),
        ("flag", "Medium length text", "true"),
    ]
    result = ingredients_to_terminal(rows)
    data_lines = [
        re.sub(r"\x1b\[[0-9;]*m", "", ln)
        for ln in result.splitlines()
        if ln.strip() and not ln.strip().startswith("Name")
    ]
    # All data lines must be the same width (left-padded to same column structure)
    widths = [len(ln) for ln in data_lines if ln]
    assert len(set(widths)) == 1, f"Column widths differ: {widths}"


def test_ingredients_to_terminal_accepts_structured_rows():
    """ingredients_to_terminal must accept list[tuple[str,str,str]], not a GFM string."""
    rows = [("param", "A description", "default")]
    result = ingredients_to_terminal(rows)
    assert isinstance(result, str)
    assert "param" in result
    assert "A description" in result
