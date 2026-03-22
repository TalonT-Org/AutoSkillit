"""Tests for core/_terminal_table.py — the L0 shared table primitive."""

from __future__ import annotations


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
