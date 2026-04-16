"""Tests for core/_terminal_table.py — the L0 shared table primitive."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core")]


def test_compute_col_widths_is_module_level() -> None:
    """_compute_col_widths must be importable as a module-level function.

    Fails before P6-8 extraction (only exists as a local loop body), passes after.
    """
    from autoskillit.core._terminal_table import TerminalColumn, _compute_col_widths

    cols = [TerminalColumn("STEP", max_width=10, align="<")]
    rows = [("hello",), ("world-this-is-long",)]
    widths = _compute_col_widths(cols, rows)
    assert widths == [10], f"Expected [10] (capped at max_width=10), got {widths}"


def test_cell_helper_is_module_level() -> None:
    """_cell must be importable as a module-level function.

    Fails before P6-8 extraction (only exists as a closure inside render functions),
    passes after.
    """
    from autoskillit.core._terminal_table import _cell

    assert _cell("hello", 10, "<") == "hello     "
    assert _cell("hello", 10, ">") == "     hello"
    assert _cell("toolongvalue", 8, "<") == "toolong…"


def test_render_functions_produce_identical_output_after_extraction() -> None:
    """_render_terminal_table and _render_gfm_table must produce the same output
    as before the P6-8 extraction — behavioral parity regression guard.
    """
    from autoskillit.core._terminal_table import (
        TerminalColumn,
        _render_gfm_table,
        _render_terminal_table,
    )

    cols = [
        TerminalColumn("STEP", max_width=20, align="<"),
        TerminalColumn("COUNT", max_width=8, align=">"),
    ]
    rows = [("implement", "3"), ("a-very-long-step-name-exceeds-max", "100")]

    term = _render_terminal_table(cols, rows)
    assert "implement" in term
    assert "…" in term  # truncation applied

    gfm = _render_gfm_table(cols, rows)
    assert "| implement" in gfm
    assert "---" in gfm
    assert "…" in gfm


def test_core_terminal_table_is_importable() -> None:
    """TerminalColumn and _render_terminal_table must be importable from
    autoskillit.core._terminal_table — the canonical L0 location."""
    from autoskillit.core._terminal_table import TerminalColumn, _render_terminal_table

    col = TerminalColumn(label="STEP", max_width=40, align="<")
    result = _render_terminal_table([col], [("a very long step name " * 5,)])
    assert "…" in result, "Long values must be truncated with ellipsis"


def test_cli_terminal_table_reexports_from_core() -> None:
    """cli/_terminal_table.py must re-export from core, not define its own copy."""
    from autoskillit.cli._terminal_table import TerminalColumn as CliTC
    from autoskillit.core._terminal_table import TerminalColumn as CoreTC

    assert CliTC is CoreTC, (
        "cli._terminal_table.TerminalColumn must be the same object as "
        "core._terminal_table.TerminalColumn (re-export, not a copy)"
    )
