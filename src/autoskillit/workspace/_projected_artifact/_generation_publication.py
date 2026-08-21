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
from pathlib import Path

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    LegacyRetiringEvidence,
    ManagedHome,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactRetirementEngine,
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    directory_tree_digest,
    generation_artifact_root,
    generation_plugin_selector_path,
    generation_selector_path,
    generation_store_root,
    generation_version_root,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    log_plugin_artifact_lifecycle,
    managed_home_for,
    new_plugin_artifact_incarnation_id,
    read_installed_plugin_artifact_identity,
    resolve_current_generation,
    resolve_current_generation_for_plugin,
)
from autoskillit.workspace._installed_artifact import (
    write_installed_plugin_artifact_manifest_locked,
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
    home: Path,
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
    version_root = generation_version_root(home, artifact_ref, version)
    selector = generation_selector_path(home, artifact_ref, version)
    lease_path = installed_plugin_artifact_lease_path(generation_root)
    safe_to_discard = True
    try:
        _fsync_directory(version_root)
        with ArtifactLease.acquire_exclusive(lease_path, blocking=True):
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

            prior_target = resolve_current_generation(home, artifact_ref, version)
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
    home_root = Path(home)
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
        staged_digest = directory_tree_digest(content_root)

        # Fsync staged contents for durability before the rename
        _fsync_tree_contents(content_root)

        # Move staging content to the final generation path
        os.rename(staging / "content", generation_root)
        return _finalize_generation(
            home=home_root,
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
    version_root = generation_version_root(home, install_ref, version)
    expected_root = generation_artifact_root(home, install_ref, version, incarnation_id)
    if generation_root != expected_root:
        raise ValueError(f"install-root generation must be materialized at {expected_root}")
    version_root.mkdir(parents=True, exist_ok=True)

    staged_digest = directory_tree_digest(
        generation_root, allow_symlinks=True, ignore_bytecode=True
    )
    _fsync_tree_contents(generation_root)

    return _finalize_generation(
        home=home,
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


def _select_plugin_generation(home: Path, plugin_ref: str, generation_root: Path) -> None:
    """Point the version-independent selector at the newly published generation.

    Best-effort, mirroring the retirement enqueue below it: the per-version flip
    is already durable and must not be rolled back if this one fails. A
    persistent failure fails safe — the stale target stays protected by
    ``_is_selected_generation`` and is over-retained rather than reclaimed.
    """
    selector = generation_plugin_selector_path(home, plugin_ref)
    try:
        selector.parent.mkdir(parents=True, exist_ok=True)
        _replace_symlink(selector, generation_root)
    except OSError as exc:
        logger.warning(
            "generation_plugin_selector_flip_failed: %s: %s",
            selector,
            exc,
        )


def _is_selected_generation(home: Path, plugin_ref: str, path: Path) -> bool:
    """Return whether *path* is still selected and therefore must not be retired.

    Once the plugin-level selector exists it is authoritative. It names the
    live generation, and only that generation's version keeps its per-version
    selector honored (a consumer that resolved through the per-version path
    just before the plugin-level flip may still be using it).

    Before any plugin-level selector exists — a first publish, or a persistent
    flip failure — fall back to per-version protection, which over-retains
    rather than deleting something still in use.
    """
    plugin_selected = resolve_current_generation_for_plugin(home, plugin_ref)
    if plugin_selected is None:
        return path == resolve_current_generation(home, plugin_ref, path.parent.name)
    if path == plugin_selected:
        return True
    return path == resolve_current_generation(home, plugin_ref, plugin_selected.parent.name)


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
        home: Path | ManagedHome,
        plugin_ref: str,
        artifact_kind: PluginArtifactKind = PluginArtifactKind.PLUGIN_GENERATION,
    ) -> None:
        resolved_home = home if isinstance(home, ManagedHome) else managed_home_for(Path(home))
        self._home = resolved_home.root
        self._plugin_ref = plugin_ref
        self._artifact_kind = artifact_kind
        if artifact_kind is PluginArtifactKind.PLUGIN_GENERATION:
            self._retirement = PluginArtifactRetirementEngine(
                home=resolved_home,
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
        elif artifact_kind is PluginArtifactKind.INSTALL_ROOT_GENERATION:
            self._retirement = PluginArtifactRetirementEngine(
                home=resolved_home,
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
        else:
            raise ValueError(f"unsupported generation artifact kind: {artifact_kind}")

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


def prune_stale_generations(
    home: Path,
    plugin_ref: str,
    *,
    artifact_kind: PluginArtifactKind = PluginArtifactKind.PLUGIN_GENERATION,
) -> int:
    """Queue every superseded generation across all versions for retirement.

    Enqueue-only: nothing is deleted here. Actual removal flows through
    ``try_reclaim``, which re-checks the lease and exact identity under its own
    grace window.

    Must be called under ``_InstallLock`` by the caller, like
    ``publish_generation`` itself — the lock is a non-reentrant ``flock``, so
    re-acquiring it from inside a publish would deadlock against the caller.

    Called at publish time only — wiring this into the session-launch path
    would re-hash the entire backlog (a full content-tree digest per
    candidate) on every launch, since publication is the only event that can
    create staleness.
    """
    store_root = generation_store_root(home, plugin_ref)
    if not store_root.is_dir():
        return 0
    owner = GenerationArtifactRetirementOwner(
        store_root, home=home, plugin_ref=plugin_ref, artifact_kind=artifact_kind
    )
    candidates: list[Path] = []
    for version_dir in sorted(store_root.iterdir(), key=lambda item: item.name):
        if version_dir.name.startswith(".") or version_dir.is_symlink():
            continue
        if not version_dir.is_dir():
            continue
        for incarnation in sorted(version_dir.iterdir(), key=lambda item: item.name):
            if incarnation.name.startswith(".") or incarnation.is_symlink():
                continue
            if not incarnation.is_dir():
                continue
            if _is_selected_generation(home, plugin_ref, incarnation):
                continue
            candidates.append(incarnation)

    created = 0
    not_before = datetime.now(UTC) + _GENERATION_GRACE
    for candidate in candidates:
        try:
            writer = ArtifactLease.acquire_exclusive(
                owner.lease_path(candidate),
                blocking=False,
            )
        except ArtifactLeaseContention:
            continue
        except (OSError, RuntimeError) as exc:
            logger.warning(
                "generation_prune_lease_failed: %s: %s",
                candidate,
                exc,
            )
            continue
        try:
            try:
                identity = owner.identity_for_path(candidate)
            except (PluginArtifactValidationError, OSError) as exc:
                logger.warning(
                    "generation_prune_validation_failed: %s: %s",
                    candidate,
                    exc,
                )
                continue
            enqueued = owner.enqueue_retirement(identity, not_before)
            if enqueued is not None:
                created += int(enqueued.created)
        finally:
            writer.close_preserving()
    return created
