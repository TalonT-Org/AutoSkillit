"""Stale-generation reconciliation for the generation store.

Given one candidate path, decides and durably records its retirement
disposition. The dependency on ``_generation_publication.py`` stays one-way
(publication -> prune, never the reverse); ``GenerationArtifactRetirementOwner``
is reached only as a ``TYPE_CHECKING`` annotation, never a runtime value.

Co-located in ``workspace/_projected_artifact/`` per its AGENTS.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    ManagedHome,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    get_logger,
    is_canonical_plugin_artifact_incarnation_id,
    resolve_current_generation,
    resolve_current_generation_for_plugin,
)
from autoskillit.workspace._projected_artifact._artifact_residue import (
    quarantine_artifact_residue,
    residue_staging_path,
    teardown_artifact_residue,
)

if TYPE_CHECKING:
    from autoskillit.workspace._projected_artifact._generation_publication import (
        GenerationArtifactRetirementOwner,
    )

logger = get_logger(__name__)


def _is_selected_generation(home: ManagedHome, plugin_ref: str, path: Path) -> bool:
    """Return whether *path* is still selected and therefore must not be retired.

    Once the plugin-level selector exists it is authoritative. It names the
    live generation, and only that generation's version keeps its per-version
    selector honored (a consumer that resolved through the per-version path
    just before the plugin-level flip may still be using it).

    Before any plugin-level selector exists — a first publish, or a persistent
    flip failure — fall back to per-version protection, which over-retains
    rather than deleting something still in use.
    """
    plugin_selected = resolve_current_generation_for_plugin(home.root, plugin_ref)
    if plugin_selected is None:
        return path == resolve_current_generation(home.root, plugin_ref, path.parent.name)
    if path == plugin_selected:
        return True
    return path == resolve_current_generation(home.root, plugin_ref, plugin_selected.parent.name)


class _GenerationPruneDisposition(StrEnum):
    """Closed outcomes for one generation-prune candidate."""

    SKIPPED_SELECTED = "skipped_selected"
    QUEUED_FOR_RETIREMENT = "queued_for_retirement"
    ALREADY_QUEUED = "already_queued"
    RECONCILED = "reconciled"
    RESUMED = "resumed"
    DEFERRED_UNMANAGED = "deferred_unmanaged"
    DEFERRED_CONTENDED = "deferred_contended"
    DEFERRED_IO_ERROR = "deferred_io_error"
    DEFERRED_UNAVAILABLE = "deferred_unavailable"
    DEFERRED_QUEUE_UNREADABLE = "deferred_queue_unreadable"
    ALREADY_ABSENT = "already_absent"


def _generation_residue_managed_path(entry: Path) -> Path | None:
    """Recover the original generation path for one deterministic residue entry."""
    prefix, separator, suffix = entry.name.partition(".autoskillit-residue-")
    if not separator or not suffix or not prefix.startswith("."):
        return None
    incarnation_id = prefix[1:]
    if not is_canonical_plugin_artifact_incarnation_id(incarnation_id):
        return None
    managed_path = entry.parent / incarnation_id
    if entry != residue_staging_path(managed_path):
        return None
    return managed_path


def _revalidate_generation_mutation_target(
    candidate: Path,
    *,
    store_root: Path,
    version_dir: Path,
    home: ManagedHome,
    plugin_ref: str,
) -> _GenerationPruneDisposition | None:
    """Recheck a generation immediately before moving it to residue."""
    try:
        if (
            candidate.parent != version_dir
            or version_dir.parent != store_root
            or store_root.is_symlink()
            or version_dir.is_symlink()
            or not version_dir.is_dir()
            or not is_canonical_plugin_artifact_incarnation_id(candidate.name)
        ):
            return _GenerationPruneDisposition.DEFERRED_UNMANAGED
        present = candidate.exists() or candidate.is_symlink()
        if not present:
            return _GenerationPruneDisposition.ALREADY_ABSENT
        if candidate.is_symlink() or not candidate.is_dir():
            return _GenerationPruneDisposition.DEFERRED_UNMANAGED
        if _is_selected_generation(home, plugin_ref, candidate):
            return _GenerationPruneDisposition.SKIPPED_SELECTED
    except OSError:
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    return None


def _quarantine_invalid_generation(
    candidate: Path,
    *,
    store_root: Path,
    version_dir: Path,
    home: ManagedHome,
    plugin_ref: str,
    owner: GenerationArtifactRetirementOwner,
) -> _GenerationPruneDisposition:
    """Durably dispose of a revalidated malformed, unselected generation."""
    refusal = _revalidate_generation_mutation_target(
        candidate,
        store_root=store_root,
        version_dir=version_dir,
        home=home,
        plugin_ref=plugin_ref,
    )
    if refusal is not None:
        return refusal

    staging = residue_staging_path(candidate)
    manifest = owner.manifest_path(candidate)
    try:
        staging_present = staging.exists() or staging.is_symlink()
        if staging_present:
            if staging.is_symlink() or not staging.is_dir():
                return _GenerationPruneDisposition.DEFERRED_IO_ERROR
            teardown_artifact_residue(staging=staging, manifest=manifest)
            refusal = _revalidate_generation_mutation_target(
                candidate,
                store_root=store_root,
                version_dir=version_dir,
                home=home,
                plugin_ref=plugin_ref,
            )
            if refusal is not None:
                return refusal
        quarantine_artifact_residue(
            managed_path=candidate,
            staging=staging,
            manifest=manifest,
        )
    except (OSError, RuntimeError):
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    return _GenerationPruneDisposition.RECONCILED


def _resume_generation_residue(
    entry: Path,
    *,
    managed_path: Path,
    store_root: Path,
    version_dir: Path,
    home: ManagedHome,
    plugin_ref: str,
    owner: GenerationArtifactRetirementOwner,
) -> _GenerationPruneDisposition:
    """Resume a rename-committed generation residue transition."""
    try:
        if (
            entry.parent != version_dir
            or version_dir.parent != store_root
            or store_root.is_symlink()
            or version_dir.is_symlink()
            or not version_dir.is_dir()
            or managed_path.exists()
            or managed_path.is_symlink()
        ):
            return _GenerationPruneDisposition.DEFERRED_UNMANAGED
        present = entry.exists() or entry.is_symlink()
        if not present:
            return _GenerationPruneDisposition.ALREADY_ABSENT
        if entry.is_symlink() or not entry.is_dir():
            return _GenerationPruneDisposition.DEFERRED_UNMANAGED
        writer = ArtifactLease.acquire_exclusive(
            owner.lease_path(managed_path),
            timeout=0.0,
        )
    except ArtifactLeaseContention:
        return _GenerationPruneDisposition.DEFERRED_CONTENDED
    except (OSError, RuntimeError):
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    try:
        if (
            managed_path.exists()
            or managed_path.is_symlink()
            or entry.is_symlink()
            or not entry.is_dir()
            or _is_selected_generation(home, plugin_ref, managed_path)
        ):
            return _GenerationPruneDisposition.DEFERRED_UNMANAGED
        teardown_artifact_residue(
            staging=entry,
            manifest=owner.manifest_path(managed_path),
        )
    except (OSError, RuntimeError):
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    finally:
        writer.close_preserving()
    return _GenerationPruneDisposition.RESUMED


def _reconcile_generation_candidate(
    candidate: Path,
    *,
    store_root: Path,
    version_dir: Path,
    home: ManagedHome,
    plugin_ref: str,
    owner: GenerationArtifactRetirementOwner,
    not_before: datetime,
) -> _GenerationPruneDisposition:
    """Return exactly one durable or deferred result for a generation candidate."""
    residue_managed_path = _generation_residue_managed_path(candidate)
    if residue_managed_path is not None:
        return _resume_generation_residue(
            candidate,
            managed_path=residue_managed_path,
            store_root=store_root,
            version_dir=version_dir,
            home=home,
            plugin_ref=plugin_ref,
            owner=owner,
        )

    refusal = _revalidate_generation_mutation_target(
        candidate,
        store_root=store_root,
        version_dir=version_dir,
        home=home,
        plugin_ref=plugin_ref,
    )
    if refusal is not None:
        return refusal
    try:
        writer = ArtifactLease.acquire_exclusive(
            owner.lease_path(candidate),
            timeout=0.0,
        )
    except ArtifactLeaseContention:
        return _GenerationPruneDisposition.DEFERRED_CONTENDED
    except (OSError, RuntimeError):
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    try:
        refusal = _revalidate_generation_mutation_target(
            candidate,
            store_root=store_root,
            version_dir=version_dir,
            home=home,
            plugin_ref=plugin_ref,
        )
        if refusal is not None:
            return refusal
        try:
            identity = owner.identity_for_path(candidate)
        except PluginArtifactValidationError:
            return _quarantine_invalid_generation(
                candidate,
                store_root=store_root,
                version_dir=version_dir,
                home=home,
                plugin_ref=plugin_ref,
                owner=owner,
            )
        except PluginArtifactUnavailableError:
            return _GenerationPruneDisposition.DEFERRED_UNAVAILABLE
        except (OSError, RuntimeError):
            return _GenerationPruneDisposition.DEFERRED_IO_ERROR
        enqueued = owner.enqueue_retirement(identity, not_before)
        if enqueued is None:
            return _GenerationPruneDisposition.DEFERRED_QUEUE_UNREADABLE
        if enqueued.created:
            return _GenerationPruneDisposition.QUEUED_FOR_RETIREMENT
        return _GenerationPruneDisposition.ALREADY_QUEUED
    except (OSError, RuntimeError):
        return _GenerationPruneDisposition.DEFERRED_IO_ERROR
    finally:
        writer.close_preserving()


def _log_generation_prune_reconcile(
    candidate: Path,
    *,
    disposition: _GenerationPruneDisposition,
) -> None:
    """Emit the sole lifecycle event for one generation-prune attempt."""
    fields = {"path": str(candidate), "disposition": disposition.value}
    if disposition in {
        _GenerationPruneDisposition.RECONCILED,
        _GenerationPruneDisposition.DEFERRED_IO_ERROR,
        _GenerationPruneDisposition.DEFERRED_UNAVAILABLE,
        _GenerationPruneDisposition.DEFERRED_QUEUE_UNREADABLE,
    }:
        logger.warning("generation_prune_reconcile", **fields)
    else:
        logger.debug("generation_prune_reconcile", **fields)
