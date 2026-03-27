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


@contextlib.contextmanager
def terminal_guard() -> Generator[None, None, None]:
    """Save and restore terminal state around an interactive subprocess.

    Exit-only safety net: this guard emits NO entry-side terminal mode-switch
    sequences. The subprocess is the sole owner of alt-screen entry (e.g.,
    Claude Code's Ink TUI emits its own \\033[?1049h on startup). Emitting
    \\033[?1049h before the subprocess would cause double-emission of DECSET
    1049, overwriting the DECSC cursor save point set by Ink and corrupting its
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
                sys.stdout.write("\033[?1049l\033[?1l\033>\033[0m\033[?25h")
                sys.stdout.flush()
            except OSError:
                pass
