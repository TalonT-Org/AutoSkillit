"""Re-exports from core._terminal_table for cli/ consumers.

The canonical implementation lives at core/_terminal_table (L0).
This shim preserves backward-compatible imports for cli/ callers.
"""

from autoskillit.core import TerminalColumn, _render_terminal_table

__all__ = ["TerminalColumn", "_render_terminal_table"]
