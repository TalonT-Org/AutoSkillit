"""Terminal state management for interactive subprocess sessions.

Provides terminal_guard() — a context manager that saves and restores kernel
TTY attributes (termios) and emits VT100 reset sequences around an interactive
subprocess launch. Safe in non-TTY environments (CI, pipes).

Follows the same resource-lifecycle pattern as execution/_process_io.py's
create_temp_io() context manager.
"""

from __future__ import annotations

import contextlib
import os
import sys
import termios
from collections.abc import Generator

from autoskillit.core import get_logger

_log = get_logger(__name__)

_KITTY_TERMINALS = frozenset({"kitty", "WezTerm", "ghostty", "iTerm.app"})

# Base VT100 reset — universally safe no-ops on all terminal emulators.
_BASE_RESET = (
    "\033[?1049l"  # exit alternate screen buffer (defensive)
    "\033[?2004l"  # disable bracketed paste mode
    "\033[?1000l"  # disable normal mouse tracking
    "\033[?1002l"  # disable button-event mouse tracking
    "\033[?1003l"  # disable any-event mouse tracking
    "\033[?1006l"  # disable SGR extended mouse protocol
    "\033[?1004l"  # disable focus in/out events
    "\033[?2026l"  # disable synchronized output
    "\033[?1l"  # disable application cursor keys (DECCKM)
    "\033>"  # numeric keypad mode (DECKPNM)
    "\033[!p"  # DECSTR soft reset (18 DEC attributes, no screen clear)
    "\033[0m"  # reset SGR attributes
    "\033[?25h"  # show cursor (DECTCEM)
)

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
                _log.debug("tcsetattr failed; falling back to stty sane")
                os.system("stty sane 2>/dev/null")
        if fd is not None:
            try:
                sys.stdout.write(_BASE_RESET)
                if (
                    os.environ.get("KITTY_WINDOW_ID")
                    or os.environ.get("TERM_PROGRAM", "") in _KITTY_TERMINALS
                ):
                    sys.stdout.write(_KITTY_RESET)
                sys.stdout.flush()
            except OSError:
                pass
