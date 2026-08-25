"""Machine-scoped, content-addressed hardlink store for verbatim plugin assets.

91 separate projections each carried their own ~3 MB copy of the identical
``mermaid.min.js`` (838 MB total across 94 projections, ~74% of one live generation's
bytes). ``is_projected_asset`` (``_projection_cache.py``) already defines the shareable
set exactly -- the ~12 MB of ``assets/``, ``hooks/``, ``recipes/``, ``agents/`` any two
projections of the same release share byte-for-byte. This module hardlinks that set from
one shared store instead of copying it per projection; the store's bytes stay live as
long as any projection still references them, since unlinking a hardlinked name only
decrements the link count.

Two hard requirements on placement, both load-bearing:

- The store must NOT live inside ``projections_root`` --
  ``prune_stale_projections`` (``_projection_cache.py``) enumerates that root and
  retires whatever it finds there; commit ``0949f8a8f`` (#4689/#4690) already fixed
  exactly this mistake once (a plugin-generations store misidentified as a stale
  projection). Placed outside, disjoint by construction.
- The store must be on the SAME DEVICE as ``projections_root`` -- ``os.link()`` raises
  ``EXDEV`` across filesystems, and the projections root's actual device varies (the
  real ``$HOME`` in production; a `--basetemp`-scoped isolated home in tests, one that
  the plan explicitly requires the store to survive being materialized under two
  *different* isolated homes -- see C1). Resolved from ``tempfile.gettempdir()``, not a
  ``$HOME``-relative literal, with an explicit ``st_dev`` equality check: a mismatch is
  logged loudly and treated as "no store available" (callers fall back to `copy2`
  wholesale) rather than attempting and catching `EXDEV` once per file.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from autoskillit.core import ArtifactLease, ArtifactLeaseContention, get_logger

from ._projection_cache import per_file_asset_digest

logger = get_logger(__name__)

__all__ = [
    "SHARED_ASSET_STORE_DIRNAME",
    "link_or_copy_asset",
    "resolve_shared_asset_store_root",
]

#: Sibling-of-tempdir directory name for the store; never nested under projections_root.
SHARED_ASSET_STORE_DIRNAME = "autoskillit-shared-assets"

#: Bounded wait for populating a store entry -- #4511 traced a store-wide capacity
#: exhaustion to an un-timeboxed flock on a hot path under concurrent xdist x worktree
#: load; this is the per-headless-launch path exactly that pattern would reproduce.
_STORE_LEASE_TIMEOUT_SECONDS = 2.0


def resolve_shared_asset_store_root(projections_root: Path) -> Path | None:
    """Resolve the shared store's root, or None when no same-device candidate exists.

    None is a legitimate, expected outcome (an exotic filesystem layout, or a
    projections_root whose parent cannot be probed yet) -- callers must treat it as
    "skip linking, fall back to copy2 for every file this call", not retry-with-a-
    different-root, which would silently defeat the whole mechanism.
    """
    parent = projections_root.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        projections_device = os.stat(parent).st_dev
    except OSError as exc:
        logger.warning(
            "shared_asset_store_projections_root_unprobeable",
            path=str(parent),
            error=str(exc),
        )
        return None

    candidate = Path(tempfile.gettempdir()) / SHARED_ASSET_STORE_DIRNAME
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        candidate_device = os.stat(candidate).st_dev
    except OSError as exc:
        logger.warning(
            "shared_asset_store_candidate_unprobeable", path=str(candidate), error=str(exc)
        )
        return None

    if candidate_device != projections_device:
        logger.warning(
            "shared_asset_store_device_mismatch",
            candidate=str(candidate),
            candidate_device=candidate_device,
            projections_root_device=projections_device,
        )
        return None
    return candidate


def link_or_copy_asset(source: Path, destination: Path, *, store_root: Path | None) -> None:
    """Populate `destination` from `source`, sharing a hardlink store entry when possible.

    Falls back to a verbatim `shutil.copy2` whenever the store is unavailable, the
    device check failed (`store_root` is None), the populate-lease times out, or
    `os.link` itself fails (EXDEV, a filesystem without hardlink support, or any other
    OSError) -- the fallback is the correctness backstop; sharing is purely an
    optimization layered on top of it.
    """
    if store_root is None:
        shutil.copy2(source, destination)
        return

    try:
        digest = per_file_asset_digest(source)
    except OSError:
        shutil.copy2(source, destination)
        return

    store_entry = store_root / digest
    if not store_entry.exists():
        _populate_store_entry(source, store_entry, store_root=store_root)

    try:
        os.link(store_entry, destination)
    except OSError:
        shutil.copy2(source, destination)


def _populate_store_entry(source: Path, store_entry: Path, *, store_root: Path) -> None:
    """Best-effort: create `store_entry` by hardlinking `source` under a bounded lease.

    Never raises -- a failure here just means the immediate caller falls through to its
    own `os.link(store_entry, ...)` attempt, which will itself fail (store_entry still
    absent) and fall back to `copy2`. A concurrent populator winning the race is the
    common, expected case (`store_entry.exists()` becomes true under us), not an error.
    """
    lease_path = store_root / f".{store_entry.name}.lock"
    try:
        lease = ArtifactLease.acquire_exclusive(lease_path, timeout=_STORE_LEASE_TIMEOUT_SECONDS)
    except ArtifactLeaseContention:
        return
    except OSError as exc:
        logger.warning("shared_asset_store_lease_failed", path=str(lease_path), error=str(exc))
        return
    try:
        if store_entry.exists():
            return
        staging = store_entry.with_name(f".{store_entry.name}.staging-{os.getpid()}")
        try:
            os.link(source, staging)
            os.replace(staging, store_entry)
        except OSError as exc:
            logger.warning(
                "shared_asset_store_populate_failed", path=str(store_entry), error=str(exc)
            )
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        lease.close_preserving()
