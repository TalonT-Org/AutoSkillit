"""Timed prompt primitive with TTY guard, ANSI formatting, and timeout.

Every user-facing ``input()`` in the CLI layer must go through
``timed_prompt()`` rather than calling ``input()`` directly.  A structural
test (``test_input_tty_contracts.py``) enforces this invariant.

``status_line()`` is the "pre-flight feedback" primitive — it prints a
single status message before any blocking I/O so the user always sees
output immediately.
"""

from __future__ import annotations

import select
import sys

from autoskillit.cli._ansi import supports_color
from autoskillit.cli._init_helpers import _require_interactive_stdin


def timed_prompt(
    text: str,
    *,
    default: str = "",
    timeout: int = 30,
    label: str = "prompt",
) -> str:
    """Display a formatted prompt with a bounded wait.

    Composes three invariants into one call:

    1. **TTY guard** — calls ``_require_interactive_stdin(label)``.
    2. **ANSI formatting** — bold prompt, dim timeout hint.
    3. **Timeout** — ``select.select`` bounds the wait; returns *default*
       on expiry instead of blocking forever.

    Parameters
    ----------
    text
        The prompt text shown to the user (plain text — ANSI is applied
        automatically based on ``supports_color()``).
    default
        Value returned when the user does not respond within *timeout*
        seconds, or on ``EOFError``.
    timeout
        Maximum seconds to wait for input.  ``0`` disables the timeout
        (waits indefinitely — use only for prompts the user explicitly
        initiated).
    label
        Human-readable label for the TTY-guard error message.
    """
    _require_interactive_stdin(label)

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _D = "\x1b[2m" if color else ""
    _R = "\x1b[0m" if color else ""

    hint = f" {_D}(auto-continues in {timeout}s){_R}" if timeout else ""
    formatted = f"{_B}{text}{_R}{hint} "
    print(formatted, end="", flush=True)

    if timeout:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            print(f"\n{_D}(timed out, continuing...){_R}", flush=True)
            return default

    try:
        return input("").strip().lower()
    except EOFError:
        return default


def status_line(message: str) -> None:
    """Print a single-line status message with appropriate formatting.

    This is the "pre-flight feedback" primitive: call it before any
    blocking I/O so the terminal is never silent.
    """
    color = supports_color()
    _D = "\x1b[2m" if color else ""
    _R = "\x1b[0m" if color else ""
    print(f"{_D}{message}{_R}", flush=True)
