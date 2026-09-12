"""Session-skill lease lifecycle primitives.

Owns the lease data class, lock path subdir constant, persistent-root
resolution helpers, and stateless lease/removal primitives used by cleanup
and validation. The manager retains root ordering derived from
``(self._root, *self._persistent_roots.values())`` and passes candidate
roots into these lifecycle primitives.

Lifecycle is intentionally independent of manager-owned state — manager map
mutation and ``_InitializedSession`` consumption stay with the manager
shard to avoid a lifecycle-to-manager runtime import.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    ArtifactLease,
    ArtifactLeaseContention,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

logger = get_logger(__name__)

_SESSION_LEASES_SUBDIR = ".session-leases"


def _raise_failures(message: str, failures: list[BaseException]) -> None:
    """Raise one failure unchanged, or preserve ordered failures as a group."""
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0]
    raise BaseExceptionGroup(message, failures)


def _remove_and_verify(path: Path) -> bool:
    """Remove a generated home and prove that no directory entry remains."""
    if not os.path.lexists(path):
        return False
    if path.is_symlink():
        raise RuntimeError(f"Refusing to recursively remove symlinked session home: {path}")
    shutil.rmtree(path)
    if os.path.lexists(path):
        raise RuntimeError(f"Session home still exists after removal: {path}")
    return True


def resolve_persistent_session_root(
    base_root: Path,
    backend: CodingAgentBackend,
) -> Path | None:
    """Resolve a backend-declared persistent generated-home root."""
    if not backend.capabilities.session_dir_persistent:
        return None
    subdir = backend.conventions.persistent_session_root_subdir
    if subdir is None:
        raise RuntimeError("Persistent backend has no generated-home root convention")
    if subdir.is_absolute() or ".." in subdir.parts:
        raise RuntimeError(f"Unsafe persistent generated-home root convention: {subdir}")
    return base_root / subdir


def resolve_persistent_session_roots(
    base_root: Path,
    backends: Iterable[CodingAgentBackend],
    *,
    required_backend_names: AbstractSet[str] = frozenset(),
) -> dict[str, Path]:
    """Resolve persistent generated-home roots for every persistent backend.

    A backend whose root convention is malformed is skipped unless its name is
    in required_backend_names, in which case the RuntimeError propagates —
    construction sites require their own load-bearing backend to be resolvable
    while deferring pinned-backend enforcement to preflight/doctor validation.
    """
    roots: dict[str, Path] = {}
    for backend in backends:
        try:
            root = resolve_persistent_session_root(base_root, backend)
        except RuntimeError:
            if backend.name in required_backend_names:
                raise
            logger.warning(
                "persistent_root_unresolvable_for_backend",
                backend=backend.name,
                exc_info=True,
            )
            continue
        if root is not None:
            roots[backend.name] = root
    return roots


@dataclass(slots=True)
class _SessionLease:
    """Workspace-owned external lease for a removable generated home."""

    lease: ArtifactLease

    @property
    def path(self) -> Path:
        return self.lease.path

    @property
    def fd(self) -> int | None:
        return self.lease.fd

    @classmethod
    def acquire(
        cls,
        lock_path: Path,
        *,
        blocking: bool,
    ) -> _SessionLease | None:
        try:
            lease = ArtifactLease.acquire_exclusive(
                lock_path,
                timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS if blocking else 0.0,
            )
        except ArtifactLeaseContention:
            return None
        except BaseException:
            logger.error("session_lease_acquisition_failed", exc_info=True)
            raise
        return cls(lease=lease)

    def release(self) -> None:
        try:
            self.lease.close()
        except BaseException:
            logger.error("session_lease_close_failed", exc_info=True)
            raise


__all__ = [
    "_SESSION_LEASES_SUBDIR",
    "_SessionLease",
    "_raise_failures",
    "_remove_and_verify",
    "resolve_persistent_session_root",
    "resolve_persistent_session_roots",
]
