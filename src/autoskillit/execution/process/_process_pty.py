"""PTY wrapping utilities for subprocess commands."""

from __future__ import annotations

import shlex
import shutil
import sys


def pty_wrap_command(cmd: list[str]) -> list[str]:
    """Wrap a command with ``script`` to provide a PTY.

    Returns the original command unchanged if ``script`` is not available.

    Uses platform-appropriate flags:
    - Linux (GNU util-linux): ``script -qefc "<cmd>" /dev/null``
    - macOS (BSD script): ``script -q /dev/null command [args...]``
    """
    script_path = shutil.which("script")
    if script_path is None:
        return cmd
    if sys.platform == "darwin":
        # BSD script: transcript file precedes the command; args passed directly
        return [script_path, "-q", "/dev/null"] + cmd
    # GNU script: -e propagates exit code, -f flushes, -c accepts a shell string
    escaped = " ".join(shlex.quote(c) for c in cmd)
    return [script_path, "-qefc", escaped, "/dev/null"]
