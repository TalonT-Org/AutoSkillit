"""Terminal state management for interactive subprocess sessions.

Provides terminal_guard() — a context manager that saves and restores kernel
TTY attributes (termios) and emits VT100 reset sequences around an interactive
subprocess launch. Safe in non-TTY environments (CI, pipes).

Follows the same resource-lifecycle pattern as execution/_process_io.py's
create_temp_io() context manager.
"""

from __future__ import annotations

import contextlib
import enum
import os
import sys
import termios
from collections.abc import Generator
from typing import NamedTuple

from autoskillit.core import get_logger

logger = get_logger(__name__)

_KBP_TERMINALS = frozenset({"kitty", "WezTerm", "ghostty", "iTerm.app"})


class ResetLayer(enum.StrEnum):
    """Terminal state layers that must be fully reset on exit.

    Each layer represents a category of terminal state that Claude Code's
    Ink TUI may modify during its session. The terminal_guard() context
    manager must emit at least one reset sequence per layer.

    Reference: ECMA-48 (5th ed.), xterm ctlseqs (Thomas Dickey), VT510 spec.
    """

    SCREEN_BUFFER = "screen_buffer"
    PRIVATE_MODE = "private_mode"
    TERMINAL_STATE = "terminal_state"
    CONTENT = "content"


class ResetEntry(NamedTuple):
    """A single VT100 reset sequence with its layer classification."""

    sequence: str
    name: str
    layer: ResetLayer


_RESET_SPEC: tuple[ResetEntry, ...] = (
    # --- SCREEN_BUFFER layer ---
    ResetEntry("\033[?1049l", "rmcup", ResetLayer.SCREEN_BUFFER),
    ResetEntry("\033[?2026l", "sync_output_off", ResetLayer.SCREEN_BUFFER),
    # --- PRIVATE_MODE layer ---
    ResetEntry("\033[?2004l", "bracketed_paste_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1000l", "mouse_normal_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1002l", "mouse_button_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1003l", "mouse_any_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1006l", "mouse_sgr_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1004l", "focus_events_off", ResetLayer.PRIVATE_MODE),
    ResetEntry("\033[?1l", "decckm_off", ResetLayer.PRIVATE_MODE),
    # --- TERMINAL_STATE layer ---
    ResetEntry("\033>", "deckpnm", ResetLayer.TERMINAL_STATE),
    ResetEntry("\033[r", "decstbm_reset", ResetLayer.TERMINAL_STATE),
    ResetEntry("\033[!p", "decstr", ResetLayer.TERMINAL_STATE),
    ResetEntry("\033(B", "g0_charset_ascii", ResetLayer.TERMINAL_STATE),
    ResetEntry("\033[0m", "sgr_reset", ResetLayer.TERMINAL_STATE),
    ResetEntry("\033[?25h", "dectcem_show_cursor", ResetLayer.TERMINAL_STATE),
    # --- CONTENT layer (must be last) ---
    ResetEntry("\033[H", "cursor_home", ResetLayer.CONTENT),
    ResetEntry("\033[J", "erase_to_end", ResetLayer.CONTENT),
)

# Derived constant — computed from the spec, not hand-maintained.
_BASE_RESET = "".join(entry.sequence for entry in _RESET_SPEC)

# Kitty keyboard protocol teardown — NOT universally safe. JediTerm
# (JetBrains IDEs) echoes literal chars from these sequences
# (claude-code#18135). Only emit when terminal is known to support KBP.
_KITTY_RESET = (
    "\033[=0u"  # hard-disable Kitty keyboard protocol
    "\033[<99u"  # drain Kitty keyboard protocol push stack
)


@contextlib.contextmanager
def terminal_guard() -> Generator[None, None, None]:
    """Save and restore terminal state around an interactive subprocess.

    Exit-only safety net: this guard emits NO entry-side terminal mode-switch
    sequences. The subprocess is the sole owner of alt-screen entry (e.g.,
    Claude Code's Ink TUI emits its own smcup sequence on startup). Emitting
    smcup before the subprocess would cause double-emission of DECSET 1049,
    overwriting the DECSC cursor save point set by Ink and corrupting its
    viewport height calculation (DECSET 1049 is a boolean toggle — no nesting
    counter exists in any terminal emulator).

    On entry: saves termios TTY attributes (kernel TTY discipline).
    On exit (any path, including exceptions): restores saved attributes and
    emits VT100 reset sequences as a safety net for application-mode escape
    sequences that Claude Code may have sent but not reverted on abnormal exit.

    Safe to call when stdin is not a real TTY (pipes, CI, headless tests):
    detection is done gracefully and the context manager becomes a no-op.
    """
    fd = None
    old_settings = None

    if sys.stdin.isatty():
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
        except (termios.error, OSError, TypeError):
            fd = None

    try:
        yield
    finally:
        if fd is not None and old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSAFLUSH, old_settings)
            except termios.error:
                logger.debug("tcsetattr failed; falling back to stty sane")
                os.system("stty sane 2>/dev/null")
        if fd is not None:
            try:
                sys.stdout.write(_BASE_RESET)
                if (
                    os.environ.get("KITTY_WINDOW_ID")
                    or os.environ.get("TERM_PROGRAM", "") in _KBP_TERMINALS
                ):
                    sys.stdout.write(_KITTY_RESET)
                sys.stdout.flush()
            except OSError:
                pass
