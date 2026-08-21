"""Sealed install identity for the current process (issue #4597).

Modeled on :class:`ExecutableLaunchBinding` (``core/types/_type_backend.py``,
sealed by ``core/runtime/executable_binding.py``): capture the process's
install identity once, at first access, and never re-derive it. Unlike that
binding — which additionally seals ``size``/``mtime_ns``/``file_sha256`` for
one launched binary — this only needs enough to detect *replacement* of the
install root a long-lived process reads from: ``device``/``inode``, plus the
version string captured at the same instant, so version and identity can
never be read from two different points in time. That two-different-times
shape is exactly what ARCH-012 forbids and what ``assert_generator_process_
fresh()`` previously did by reading live state twice.

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

Issue #4597 Phase 3: the same first-access seal also best-effort acquires and
holds a shared ``ArtifactLease`` on this process's own install-root
generation (see ``_acquire_self_lease``), for the process's entire lifetime.
This is what makes the retirement engine's reclaim sweep durably refuse to
delete a root a live process is reading from, independent of the 24-hour
retirement grace window or how many subsequent versions have superseded it.
"""

from __future__ import annotations

import functools
import importlib.metadata
import importlib.resources as ir
from dataclasses import dataclass
from pathlib import Path

from .logging import get_logger

logger = get_logger(__name__)

_SELF_LEASE_HANDLE: object | None = None
"""Process-lifetime handle for the self-held lease. Intentionally never
explicitly released — the file descriptor closes naturally at process exit, which
is exactly when it should stop protecting this root."""


def _acquire_self_lease(root: Path, version: str) -> None:
    """Best-effort: hold a shared lease on this process's own install-root
    generation for the process's lifetime.

    Finds the generation by walking upward from ``root`` (this process's
    ``pkg_root()``) to the matching install-root incarnation. This uses the
    bound package location rather than the ambient home, which may have
    changed since the generation was installed.

    Best-effort and silent on any failure: a pre-Phase-3 (legacy shared-root)
    install, a local editable/dev checkout, or any process whose root simply
    isn't under the generation store has nothing to lease here.
    ``install_binding_matches_current_state()`` remains the correct backstop
    for those shapes.
    """
    global _SELF_LEASE_HANDLE
    try:
        from ._plugin_artifact_identity import installed_plugin_artifact_lease_path
        from ._plugin_ids import _AUTOSKILLIT_INSTALL_ROOT_KEY
        from .runtime.artifact_lease import ArtifactLease

        canonical_root = root.resolve()
        install_root_name = _AUTOSKILLIT_INSTALL_ROOT_KEY.partition("@")[0]
        for incarnation_dir in canonical_root.parents:
            version_root = incarnation_dir.parent
            store_root = version_root.parent
            if (
                version_root.name != version
                or store_root.name != install_root_name
                or store_root.parent.name != "plugin-generations"
                or store_root.parent.parent.name != ".autoskillit"
            ):
                continue
            _SELF_LEASE_HANDLE = ArtifactLease.acquire_existing_shared(
                installed_plugin_artifact_lease_path(incarnation_dir)
            )
            return
    except Exception:
        logger.warning("self_lease_acquisition_failed", exc_info=True)
        return


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
    version = importlib.metadata.version("autoskillit")
    _acquire_self_lease(root, version)
    return InstallBinding(
        root=root,
        version=version,
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
