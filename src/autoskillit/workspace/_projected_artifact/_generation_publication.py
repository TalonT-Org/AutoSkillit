"""Generation-keyed publication for plugin artifacts.

Stages a fresh generation at an incarnation-keyed immutable path, then
flips an atomic ``current`` symlink to select it. The flip is the sole
commit point — nothing is written into or verified against the generation
after it. Publication never contends with readers because the fresh
directory is unpublished until the flip.

Co-located in ``workspace/_projected_artifact/`` per its AGENTS.md.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    VANISHED_ERRORS,
    ArtifactLease,
    ArtifactLeaseContention,
    LegacyRetiringEvidence,
    ManagedHome,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactRetirementEngine,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    classify_directory_tree_digest_error,
    directory_tree_digest,
    generation_artifact_root,
    generation_plugin_selector_path,
    generation_selector_path,
    generation_store_root,
    generation_version_root,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    is_canonical_plugin_artifact_incarnation_id,
    log_plugin_artifact_lifecycle,
    managed_home_for,
    new_plugin_artifact_incarnation_id,
    read_installed_plugin_artifact_identity,
    resolve_current_generation,
    resolve_current_generation_for_plugin,
    scan_observed,
)
from autoskillit.workspace._installed_artifact import (
    write_installed_plugin_artifact_manifest_locked,
)
from autoskillit.workspace._projected_artifact._artifact_residue import (
    quarantine_artifact_residue,
    residue_staging_path,
    teardown_artifact_residue,
)

logger = get_logger(__name__)

_STAGING_ORPHAN_GRACE = timedelta(hours=1)

# Cross-version staleness needs a wider window than same-version churn: the
# retirement sweep runs once per MCP server startup, not on a recurring timer
# (server/_lifespan.py fires it once), so the grace must comfortably outlast the
# gap between server restarts on a lightly-used machine.
_GENERATION_GRACE = timedelta(hours=24)


def _sweep_orphaned_staging(version_root: Path) -> None:
    """Remove staging directories abandoned by a crashed ``publish_generation`` call.

    A crash between staging and the atomic flip leaves a
    ``.{incarnation_id}.staging-*`` directory that was never selected. Only
    directories older than the grace window are removed, so a staging
    directory belonging to a concurrent in-flight publish is never touched.
    """
    if not version_root.is_dir():
        return
    threshold = datetime.now(UTC) - _STAGING_ORPHAN_GRACE
    for entry in version_root.iterdir():
        if not entry.is_dir() or entry.is_symlink() or ".staging-" not in entry.name:
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime >= threshold:
            continue
        try:
            shutil.rmtree(entry)
            logger.info("generation_orphan_staging_removed: %s", entry)
        except OSError as exc:
            logger.warning(
                "generation_orphan_staging_removal_failed: %s: %s",
                entry,
                exc,
            )


def _fsync_directory(path: Path) -> None:
    """Fsync a directory for durability after renames."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_symlink(path: Path, target: Path) -> None:
    """Atomically replace a symlink via temp-link + os.replace + fsync."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.link")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    temporary.symlink_to(target)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_tree_contents(root: Path) -> None:
    """Fsync every regular file and directory entry under *root*."""
    for current_root, _dirs, files in os.walk(root):
        current = Path(current_root)
        for name in files:
            fpath = current / name
            fd = os.open(fpath, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_directory(current)


def _discard_unpublished_generation(generation_root: Path) -> None:
    """Best-effort removal of one exact generation that never became current."""
    paths = (
        installed_plugin_artifact_manifest_path(generation_root),
        installed_plugin_artifact_lease_path(generation_root),
    )
    try:
        shutil.rmtree(generation_root)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("unpublished_generation_removal_failed: %s", exc)
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("unpublished_generation_sidecar_removal_failed: %s: %s", path, exc)


def _finalize_generation(
    *,
    home: ManagedHome,
    artifact_ref: str,
    version: str,
    semantic_key: str,
    incarnation_id: str,
    generation_root: Path,
    staged_digest: str,
    action: str,
    artifact_kind: PluginArtifactKind,
    allow_symlinks: bool = False,
    ignore_bytecode: bool = False,
) -> PluginArtifactIdentity:
    """Manifest, select, and retire around the per-version selector commit point."""
    version_root = generation_version_root(home.root, artifact_ref, version)
    selector = generation_selector_path(home.root, artifact_ref, version)
    lease_path = installed_plugin_artifact_lease_path(generation_root)
    safe_to_discard = True
    try:
        _fsync_directory(version_root)
        with ArtifactLease.acquire_exclusive(
            lease_path,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        ):
            identity = write_installed_plugin_artifact_manifest_locked(
                generation_root,
                semantic_key=semantic_key,
                action=action,
                incarnation_id=incarnation_id,
                allow_symlinks=allow_symlinks,
                ignore_bytecode=ignore_bytecode,
            )
            if identity.artifact_digest != staged_digest:
                raise RuntimeError(
                    f"generation digest changed between staging and publication: "
                    f"staged {staged_digest}, published {identity.artifact_digest}"
                )

            prior_target = resolve_current_generation(home.root, artifact_ref, version)
            try:
                _replace_symlink(selector, generation_root)
            except OSError:
                safe_to_discard = False
                try:
                    if prior_target is None:
                        selector.unlink(missing_ok=True)
                        _fsync_directory(version_root)
                    else:
                        _replace_symlink(selector, prior_target)
                    safe_to_discard = True
                except OSError as restore_error:
                    logger.error(
                        "%s_selector_restore_failed: prior=%s",
                        action,
                        prior_target,
                        exc_info=restore_error,
                    )
                raise
            safe_to_discard = False
    except Exception:
        if safe_to_discard:
            _discard_unpublished_generation(generation_root)
            _fsync_directory(version_root)
        raise

    log_plugin_artifact_lifecycle(
        logger,
        action=action,
        outcome="succeeded",
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
        semantic_key=semantic_key,
        incarnation=incarnation_id,
    )
    _select_plugin_generation(home, artifact_ref, generation_root)
    prune_stale_generations(home, artifact_ref, artifact_kind=artifact_kind)
    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        manifest_schema_version=identity.manifest_schema_version,
        artifact_digest=identity.artifact_digest,
        managed_path=generation_root,
        manifest_path=installed_plugin_artifact_manifest_path(generation_root),
    )


def publish_generation(
    *,
    home: Path | ManagedHome,
    plugin_ref: str,
    version: str,
    semantic_key: str,
    source_root: Path,
) -> PluginArtifactIdentity:
    """Stage a fresh generation, verify, flip the selector, and enqueue the prior.

    Must be called under ``_InstallLock`` by the caller (not acquired here,
    so the caller can compose multiple operations under one lock).

    Returns the identity of the newly published generation.
    """
    resolved_home = home if isinstance(home, ManagedHome) else managed_home_for(Path(home))
    home_root = resolved_home.root
    incarnation_id = new_plugin_artifact_incarnation_id()
    version_root = generation_version_root(home_root, plugin_ref, version)
    generation_root = generation_artifact_root(home_root, plugin_ref, version, incarnation_id)
    version_root.mkdir(parents=True, exist_ok=True)
    _sweep_orphaned_staging(version_root)

    # Stage: copy source into the generation directory
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{incarnation_id}.staging-",
            dir=version_root,
        )
    )
    try:
        # Copy the entire source tree
        shutil.copytree(source_root, staging / "content", dirs_exist_ok=False)
        content_root = staging / "content"

        # Compute the tree digest while still in staging (pre-manifest)
        try:
            staged_digest = directory_tree_digest(content_root)
        except (OSError, ValueError) as exc:
            raise classify_directory_tree_digest_error(exc) from exc

        # Fsync staged contents for durability before the rename
        _fsync_tree_contents(content_root)

        # Move staging content to the final generation path
        os.rename(staging / "content", generation_root)
        return _finalize_generation(
            home=resolved_home,
            artifact_ref=plugin_ref,
            version=version,
            semantic_key=semantic_key,
            incarnation_id=incarnation_id,
            generation_root=generation_root,
            staged_digest=staged_digest,
            action="publish_generation",
            artifact_kind=PluginArtifactKind.PLUGIN_GENERATION,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def publish_install_root_generation(
    *,
    home: Path,
    install_ref: str,
    version: str,
    semantic_key: str,
    incarnation_id: str,
    generation_root: Path,
) -> PluginArtifactIdentity:
    """Finalize an install-root generation whose content is already staged.

    Unlike ``publish_generation``, the caller has already materialized
    ``generation_root`` directly — an installer (``uv tool install``) writes its
    own destination tree and offers no staging-directory handoff, so content
    creation happens before this function is ever called, at a location the
    caller chose via ``core.generation_artifact_root``. This performs the
    publication work that follows materialization: digest, lease, manifest,
    digest verification, the atomic selector flip (the sole commit point), the
    version-independent selector update, and enqueueing every previously
    selected generation across all versions for retirement.

    Must be called under ``_InstallLock`` by the caller, exactly like
    ``publish_generation``.
    """
    resolved_home = managed_home_for(home)
    version_root = generation_version_root(resolved_home.root, install_ref, version)
    expected_root = generation_artifact_root(
        resolved_home.root, install_ref, version, incarnation_id
    )
    if generation_root != expected_root:
        raise ValueError(f"install-root generation must be materialized at {expected_root}")
    version_root.mkdir(parents=True, exist_ok=True)

    try:
        staged_digest = directory_tree_digest(
            generation_root, allow_symlinks=True, ignore_bytecode=True
        )
    except (OSError, ValueError) as exc:
        raise classify_directory_tree_digest_error(exc) from exc
    _fsync_tree_contents(generation_root)

    return _finalize_generation(
        home=resolved_home,
        artifact_ref=install_ref,
        version=version,
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        generation_root=generation_root,
        staged_digest=staged_digest,
        action="publish_install_root_generation",
        artifact_kind=PluginArtifactKind.INSTALL_ROOT_GENERATION,
        allow_symlinks=True,
        ignore_bytecode=True,
    )


def _select_plugin_generation(home: ManagedHome, plugin_ref: str, generation_root: Path) -> None:
    """Point the version-independent selector at the newly published generation.

    Best-effort, mirroring the retirement enqueue below it: the per-version flip
    is already durable and must not be rolled back if this one fails. A
    persistent failure fails safe — the stale target stays protected by
    ``_is_selected_generation`` and is over-retained rather than reclaimed.
    """
    selector = generation_plugin_selector_path(home.root, plugin_ref)
    try:
        selector.parent.mkdir(parents=True, exist_ok=True)
        _replace_symlink(selector, generation_root)
    except OSError as exc:
        logger.warning(
            "generation_plugin_selector_flip_failed: %s: %s",
            selector,
            exc,
        )


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


class GenerationArtifactRetirementOwner:
    """Exact-identity retirement owner for the whole generation store.

    Scoped to ``generation_store_root`` — every version, not one — because the
    retirement coordinator dispatches by artifact kind to a single owner. An
    owner rooted at one version directory cannot contain records from any other,
    and ``try_reclaim`` rejects an uncontained record on every sweep forever
    without ever removing it.
    """

    def __init__(
        self,
        managed_root: Path,
        *,
        home: ManagedHome,
        plugin_ref: str,
        artifact_kind: PluginArtifactKind = PluginArtifactKind.PLUGIN_GENERATION,
    ) -> None:
        self._home = home
        self._plugin_ref = plugin_ref
        self._artifact_kind = artifact_kind
        if artifact_kind not in {
            PluginArtifactKind.PLUGIN_GENERATION,
            PluginArtifactKind.INSTALL_ROOT_GENERATION,
        }:
            raise ValueError(f"unsupported generation artifact kind: {artifact_kind}")
        if artifact_kind is PluginArtifactKind.PLUGIN_GENERATION:
            self._retirement = PluginArtifactRetirementEngine(
                home=home,
                managed_root=managed_root,
                artifact_kind=PluginArtifactKind.PLUGIN_GENERATION,
                manifest_path=self.manifest_path,
                lease_path=self.lease_path,
                current_identity=self._current_identity,
                logger=logger,
                is_current=lambda path: _is_selected_generation(
                    self._home, self._plugin_ref, path
                ),
            )
        else:
            self._retirement = PluginArtifactRetirementEngine(
                home=home,
                managed_root=managed_root,
                artifact_kind=PluginArtifactKind.INSTALL_ROOT_GENERATION,
                manifest_path=self.manifest_path,
                lease_path=self.lease_path,
                current_identity=self._current_identity,
                logger=logger,
                is_current=lambda path: _is_selected_generation(
                    self._home, self._plugin_ref, path
                ),
            )

    @property
    def managed_root(self) -> Path:
        return self._retirement.managed_root

    @staticmethod
    def manifest_path(managed_path: Path) -> Path:
        return installed_plugin_artifact_manifest_path(managed_path)

    @staticmethod
    def lease_path(managed_path: Path) -> Path:
        return installed_plugin_artifact_lease_path(managed_path)

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
    ) -> RetiringAppendResult | None:
        return self._retirement.enqueue_retirement(identity, not_before)

    def cancel_obsolete_retirements(
        self, identity: PluginArtifactIdentity
    ) -> tuple[str, ...] | None:
        return self._retirement.cancel_obsolete_retirements(identity)

    def identity_for_path(self, managed_path: Path) -> PluginArtifactIdentity:
        """Validate and return the exact current identity at a managed path."""
        managed_path = Path(managed_path)
        if not self._retirement.contains(managed_path):
            raise PluginArtifactValidationError(
                f"generation is outside managed root: {managed_path}"
            )
        return self._read_identity(managed_path)

    def _current_identity(self, record: RetiringArtifactRecord) -> PluginArtifactIdentity:
        return self._read_identity(
            record.managed_path,
            expected_semantic_key=record.semantic_key,
        )

    def _read_identity(
        self,
        managed_path: Path,
        *,
        expected_semantic_key: str | None = None,
    ) -> PluginArtifactIdentity:
        is_install_root = self._artifact_kind is PluginArtifactKind.INSTALL_ROOT_GENERATION
        return read_installed_plugin_artifact_identity(
            managed_path,
            expected_semantic_key=expected_semantic_key,
            manifest_path=self.manifest_path(managed_path),
            allow_symlinks=is_install_root,
            ignore_bytecode=is_install_root,
        )

    def try_reclaim(self, record: RetiringArtifactRecord, now: datetime) -> RetirementOutcome:
        return self._retirement.try_reclaim(record, now)

    def try_promote_legacy_evidence(
        self,
        evidence: LegacyRetiringEvidence,
        now: datetime,
    ) -> RetirementOutcome:
        return self._retirement.try_promote_legacy_evidence(
            evidence,
            now,
            identity_for_path=self.identity_for_path,
        )


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


def prune_stale_generations(
    home: ManagedHome,
    plugin_ref: str,
    *,
    artifact_kind: PluginArtifactKind = PluginArtifactKind.PLUGIN_GENERATION,
) -> int:
    """Queue valid superseded generations and reconcile invalid residue.

    Valid generations remain enqueue-only: their removal flows through
    ``try_reclaim``, which re-checks the lease and exact identity under its own
    grace window. A malformed generation is instead quarantined and removed
    under the caller's install lock after an exclusive lease and a final
    selection/containment revalidation.

    Must be called under ``_InstallLock`` by the caller, like
    ``publish_generation`` itself — the lock is a non-reentrant ``flock``, so
    re-acquiring it from inside a publish would deadlock against the caller.

    Called at publish time only — wiring this into the session-launch path
    would re-hash the entire backlog (a full content-tree digest per
    candidate) on every launch, since publication is the only event that can
    create staleness.
    """
    store_root = generation_store_root(home.root, plugin_ref)
    if not store_root.is_dir():
        return 0
    owner = GenerationArtifactRetirementOwner(
        store_root, home=home, plugin_ref=plugin_ref, artifact_kind=artifact_kind
    )
    candidates: list[tuple[Path, Path]] = []
    try:
        version_entries = sorted(scan_observed(store_root), key=lambda entry: entry.name)
        for version_entry in version_entries:
            if (
                version_entry.name.startswith(".")
                or version_entry.is_symlink
                or not version_entry.is_dir
            ):
                continue
            version_dir = version_entry.path
            incarnation_entries = sorted(scan_observed(version_dir), key=lambda entry: entry.name)
            for incarnation_entry in incarnation_entries:
                incarnation = incarnation_entry.path
                if incarnation.name.startswith("."):
                    if _generation_residue_managed_path(incarnation) is not None:
                        candidates.append((incarnation, version_dir))
                    continue
                if incarnation_entry.is_symlink or not incarnation_entry.is_dir:
                    continue
                if _is_selected_generation(home, plugin_ref, incarnation):
                    continue
                candidates.append((incarnation, version_dir))
    except VANISHED_ERRORS:
        return 0
    except OSError as exc:
        logger.warning("generation_prune_enumeration_failed: %s: %s", store_root, exc)
        return 0

    created = 0
    not_before = datetime.now(UTC) + _GENERATION_GRACE
    for candidate, version_dir in candidates:
        disposition = _reconcile_generation_candidate(
            candidate,
            store_root=store_root,
            version_dir=version_dir,
            home=home,
            plugin_ref=plugin_ref,
            owner=owner,
            not_before=not_before,
        )
        _log_generation_prune_reconcile(candidate, disposition=disposition)
        if disposition is _GenerationPruneDisposition.QUEUED_FOR_RETIREMENT:
            created += 1
    return created
