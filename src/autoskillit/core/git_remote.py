"""IL-0 canonical remote precedence for clone-isolated repositories.

Provides a synchronous resolver usable from any import layer (including
hooks that cannot use asyncio) and the single-source-of-truth constant
for remote probe ordering.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REMOTE_PRECEDENCE: tuple[str, ...] = ("upstream", "origin")


def resolve_clone_remote_name_sync(cwd: str | Path) -> str:
    """Return the git remote name to use for fetch/push operations (sync).

    Tries remotes in precedence order (upstream before origin).
    Rejects file:// URLs — those indicate a clone-isolation origin.
    Falls back to "origin" if no remote qualifies.
    """
    for name in REMOTE_PRECEDENCE:
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", name],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                continue
            url = result.stdout.strip()
            if url.startswith("file://"):
                continue
            return name
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "origin"
