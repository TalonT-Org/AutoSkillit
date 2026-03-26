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


@contextlib.contextmanager
def terminal_guard():
    """Save and restore terminal state around an interactive subprocess.

    On entry: saves termios TTY attributes (kernel TTY discipline).
    On exit (any path, including exceptions): restores saved attributes and
    emits VT100 reset sequences to undo application-mode escape sequences
    that Claude Code may have sent but not reverted on abnormal exit.

    Safe to call when stdin is not a real TTY (pipes, CI, headless tests):
    detection is done gracefully and the context manager becomes a no-op.
    """
    fd = None
    old_settings = None

    if sys.stdin.isatty():
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            fd = None

    try:
        yield
    finally:
        if fd is not None and old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSAFLUSH, old_settings)
            except termios.error:
                try:
                    os.system("stty sane 2>/dev/null")
                except Exception:
                    pass
        try:
            sys.stdout.write("\033[?1049l\033[?1l\033>\033[0m\033[?25h")
            sys.stdout.flush()
        except OSError:
            pass
