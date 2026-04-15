"""Filesystem readiness sentinel primitives for MCP server startup.

The sentinel file replaces log-line string matching as the subprocess
synchronization primitive used by integration tests. File existence is
atomic — there is no string-parse race and no wall-clock settle-sleep.

This is an L0 module: stdlib-only imports plus core.io for ``atomic_write``.
No anyio, no FastMCP, no higher-layer autoskillit imports.

Path resolution follows the same ``AUTOSKILLIT_STATE_DIR`` override pattern as
``core.kitchen_state``: tests set the env var to a ``tmp_path``-scoped
directory to isolate sentinel files from the project tree.
"""

from __future__ import annotations

import os
from pathlib import Path

from .io import atomic_write
from .kitchen_state import get_state_dir

__all__ = [
    "readiness_sentinel_path",
    "write_readiness_sentinel",
    "cleanup_readiness_sentinel",
]


def _sentinel_dir() -> Path:
    """Return the directory where sentinel files are written.

    Delegates to ``kitchen_state.get_state_dir()`` to avoid path-resolution
    drift between the two modules.
    """
    return get_state_dir()


def readiness_sentinel_path(pid: int) -> Path:
    """Return the sentinel path for a given process ID.

    The path is deterministic and pid-scoped so parallel server instances
    (e.g. during parallel test runs) do not collide.

    :param pid: The server process ID.
    :returns: Path to ``<sentinel_dir>/server_ready_{pid}.sentinel``.
    """
    return _sentinel_dir() / f"server_ready_{pid}.sentinel"


def write_readiness_sentinel() -> Path:
    """Write an empty sentinel file at the readiness path for the current process.

    Idempotent: safe to call multiple times. The file is written atomically so
    a reader observing the file exists is guaranteed the write has completed.

    :returns: The path that was written.
    """
    path = readiness_sentinel_path(os.getpid())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "")
    return path


def cleanup_readiness_sentinel(pid: int | None = None) -> None:
    """Remove the readiness sentinel file if it exists.

    Never raises on ENOENT — safe to call from a finally: block even if
    ``write_readiness_sentinel`` was never reached (e.g. exception during startup).

    :param pid: Process ID whose sentinel to remove. Defaults to ``os.getpid()``.
    """
    resolved_pid = pid if pid is not None else os.getpid()
    sentinel = readiness_sentinel_path(resolved_pid)
    try:
        sentinel.unlink()
    except FileNotFoundError:
        pass
