"""Sealed install identity for the current process (B-1, issue #4597).

Modeled on :class:`ExecutableLaunchBinding` (``core/types/_type_backend.py``,
sealed by ``core/runtime/executable_binding.py``): capture the process's
install identity once, at first access, and never re-derive it. Unlike that
binding — which additionally seals ``size``/``mtime_ns``/``file_sha256`` for
one launched binary — this only needs enough to detect *replacement* of the
install root a long-lived process reads from: ``device``/``inode``, plus the
version string captured at the same instant, so version and identity can
never be read from two different points in time. That two-different-times
shape is exactly what ARCH-012 forbids and what ``assert_generator_process_
fresh()`` used to do before B-3.

Purely in-process: the sealed binding lives only in ``lru_cache`` memory for
the interpreter's lifetime and is never written to disk, so it carries no
``DURABLE_ARTIFACT_WRITERS`` obligation (see that registry's docstring in
``core/types/_type_constants.py``).

``resolve_install_binding()`` is forced very early in every process kind by
``hook_registry.py``'s module-scope ``HOOKS_DIR = pkg_root() / "hooks"``
assignment (reached at import time via ``cli/__init__.py`` -> ``cli/_hooks.py``
and ``execution/backends/__init__.py`` -> ``execution/backends/_codex_hooks.py``)
and by the MCP server's ``run_startup_drift_check``; no additional explicit
seal-forcing call is needed at any entry point.
"""

from __future__ import annotations

import functools
import importlib.metadata
import importlib.resources as ir
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstallBinding:
    """Sealed package-root identity for the process's lifetime."""

    root: Path
    version: str
    device: int
    inode: int


@functools.lru_cache(maxsize=1)
def resolve_install_binding() -> InstallBinding:
    """Seal this process's install root, version, and identity at first access."""
    root = Path(str(ir.files("autoskillit")))
    stat_result = root.stat()
    return InstallBinding(
        root=root,
        version=importlib.metadata.version("autoskillit"),
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
    )


def install_binding_matches_current_state(binding: InstallBinding) -> bool:
    """Return whether the sealed root still owns its bound path.

    Mirrors ``executable_binding_matches_current_file()``: a device/inode
    mismatch (or the path no longer existing) means something else now
    occupies the path this process sealed — the actual replacement hazard,
    not merely a changed metadata string.
    """
    try:
        stat_result = binding.root.stat()
    except OSError:
        return False
    return stat_result.st_dev == binding.device and stat_result.st_ino == binding.inode


@dataclass(frozen=True, slots=True)
class InstallStalenessRemedy:
    """One wording for "this process's identity may be stale" (B-8).

    Shared by the sealed-binding replacement probe
    (``assert_generator_process_fresh``) and the content-hash editable-install
    detector (``recipe._api_cache._check_process_staleness``) so the two
    subsystems never hand an operator contradictory restart instructions for
    the same underlying fact. Phase 3's C-6 deletes this remedy entirely once
    immutable install roots make staleness unreachable; unifying it here
    first makes that a single deletion, not two.
    """

    remedy: str


INSTALL_STALENESS_REMEDY = InstallStalenessRemedy(
    remedy="Restart the affected process — or, inside the MCP server, call reload_session."
)
