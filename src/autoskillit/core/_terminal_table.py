"""Backward-compat shim for _terminal_table — see core.io.terminal_table."""

from autoskillit.core.io.terminal_table import (
    TerminalColumn,
    _cell,
    _compute_col_widths,
    _render_gfm_table,
    _render_terminal_table,
)

__all__ = [
    "TerminalColumn",
    "_cell",
    "_compute_col_widths",
    "_render_gfm_table",
    "_render_terminal_table",
]
