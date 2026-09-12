"""When a plugin projection goes stale, and how it's retired.

Split out of ``skill_projection`` because staleness is its own concern: the
projection cache key used to cover only skill names and digests, so a release
that changed ``recipes/``, ``agents/``, or ``hooks/`` without touching a skill
produced an identical key and the previous release's assets were silently
reused. The asset inventory and the key record that closes that gap live in
the sibling ``_projection_assets.py``, reached only by direct import (never
through ``_installed/__init__.py``'s facade) -- this module re-imports their
public surface so every existing consumer keeps resolving unchanged.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import assert_never

import regex as re

from autoskillit.core import (
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
    _InstallLock,
    classify_directory_tree_digest_error,
    directory_tree_digest,
    get_logger,
    is_canonical_plugin_artifact_digest,
    is_canonical_plugin_artifact_incarnation_id,
    read_versioned_json,
)
from autoskillit.workspace._projected_artifact._artifact_residue import (
    quarantine_artifact_residue,
    residue_staging_path,
    teardown_artifact_residue,
)

from ._projection_assets import (
    PROJECTION_CACHE_KEY_EXCLUSIONS,
    ProjectionCacheKey,
    is_projected_asset,
    iter_public_plugin_asset_files,
    per_file_asset_digest,
    public_plugin_asset_digest,
)

logger = get_logger(__name__)

__all__ = [
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ProjectionEntryClass",
    "ProjectionCacheKey",
    "ProjectionReconcileDisposition",
    "ProjectedPluginRetirementOwner",
    "classify_projection_entry",
    "is_projected_asset",
    "iter_public_plugin_asset_files",
    "per_file_asset_digest",
    "projected_artifact_lease_path",
    "projected_artifact_manifest_path",
    "projected_plugin_artifact_digest",
    "prune_stale_projections",
    "public_plugin_asset_digest",
    "read_projected_plugin_identity",
    "residue_staging_path",
]

#: Reserved grace window for lease-aware retirement.
_PROJECTION_GRACE_HOURS = 6
PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION = 2
_PROJECTION_ARTIFACT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "projection_version",
        "semantic_key",
        "incarnation_id",
        "artifact_digest",
        "skills",
    }
)

_PROJECTION_KEY_PATTERN = r"[0-9a-f]{24}"
_PROJECTION_ROOT_RE = re.compile(rf"{_PROJECTION_KEY_PATTERN}\Z")
_IDENTITY_SIDECAR_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.autoskillit-projection\.json\Z"
)
_HOOK_QUARANTINE_SIDECAR_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.autoskillit-projection\.json\."
    r"hook-quarantine-[0-9a-f]{64}\Z"
)
_PUBLICATION_STAGING_ROOT_RE = re.compile(rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.plugin-[^.]+\Z")
_PUBLICATION_STAGING_MANIFEST_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.manifest-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json\Z"
)
_RETIREMENT_STAGING_ROOT_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.autoskillit-retiring-[0-9a-f]{{16}}\Z"
)
_RESIDUE_STAGING_ROOT_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.autoskillit-residue-[0-9a-f]{{16}}\Z"
)


class ProjectionEntryClass(StrEnum):
    """Known direct-child shapes in the projected-plugin cache."""

    ACTIVE_ROOT = "active_root"
    PROJECTION_ROOT = "projection_root"
    IDENTITY_SIDECAR = "identity_sidecar"
    HOOK_QUARANTINE_SIDECAR = "hook_quarantine_sidecar"
    LEASE_DIRECTORY = "lease_directory"
    PUBLICATION_STAGING_ROOT = "publication_staging_root"
    PUBLICATION_STAGING_MANIFEST = "publication_staging_manifest"
    RETIREMENT_STAGING_ROOT = "retirement_staging_root"
    RESIDUE_STAGING_ROOT = "residue_staging_root"


class ProjectionReconcileDisposition(StrEnum):
    """Closed outcomes for reconciling one projection-cache entry."""

    SKIPPED_ACTIVE = "skipped_active"
    QUEUED_FOR_RETIREMENT = "queued_for_retirement"
    ALREADY_QUEUED = "already_queued"
    RECONCILED = "reconciled"
    RESUMED = "resumed"
    DEFERRED_UNCLASSIFIED = "deferred_unclassified"
    DEFERRED_UNMANAGED = "deferred_unmanaged"
    DEFERRED_CONTENDED = "deferred_contended"
    DEFERRED_IO_ERROR = "deferred_io_error"
    DEFERRED_UNAVAILABLE = "deferred_unavailable"
    DEFERRED_QUEUE_UNREADABLE = "deferred_queue_unreadable"
    ALREADY_ABSENT = "already_absent"


def classify_projection_entry(
    entry: Path,
    *,
    active_key: str,
) -> ProjectionEntryClass:
    """Classify one direct child of the projections root or reject its shape."""
    name = entry.name
    if _PROJECTION_ROOT_RE.fullmatch(name):
        if name == active_key:
            return ProjectionEntryClass.ACTIVE_ROOT
        return ProjectionEntryClass.PROJECTION_ROOT
    if _IDENTITY_SIDECAR_RE.fullmatch(name):
        return ProjectionEntryClass.IDENTITY_SIDECAR
    if _HOOK_QUARANTINE_SIDECAR_RE.fullmatch(name):
        return ProjectionEntryClass.HOOK_QUARANTINE_SIDECAR
    if name == ".artifact-leases":
        return ProjectionEntryClass.LEASE_DIRECTORY
    if _PUBLICATION_STAGING_ROOT_RE.fullmatch(name):
        return ProjectionEntryClass.PUBLICATION_STAGING_ROOT
    if _PUBLICATION_STAGING_MANIFEST_RE.fullmatch(name):
        return ProjectionEntryClass.PUBLICATION_STAGING_MANIFEST
    if _RETIREMENT_STAGING_ROOT_RE.fullmatch(name):
        return ProjectionEntryClass.RETIREMENT_STAGING_ROOT
    if _RESIDUE_STAGING_ROOT_RE.fullmatch(name):
        return ProjectionEntryClass.RESIDUE_STAGING_ROOT
    raise ValueError(f"unclassified projection-cache entry: {entry}")


def projected_artifact_manifest_path(managed_path: Path) -> Path:
    """Return the stable sidecar manifest for a projected root."""
    managed_path = Path(managed_path)
    return managed_path.parent / f".{managed_path.name}.autoskillit-projection.json"


def projected_artifact_lease_path(managed_path: Path) -> Path:
    """Return the stable lease sidecar for a projected root."""
    managed_path = Path(managed_path)
    return managed_path.parent / ".artifact-leases" / f"{managed_path.name}.lock"


def projected_plugin_artifact_digest(public_root: Path) -> str:
    """Hash the complete projection with the canonical artifact-tree contract."""
    try:
        return directory_tree_digest(public_root)
    except (OSError, ValueError) as exc:
        raise classify_directory_tree_digest_error(exc) from exc


def read_projected_plugin_identity(
    managed_path: Path,
    *,
    manifest_path: Path,
    expected_semantic_key: str,
    expected_projection_version: int | None = None,
) -> PluginArtifactIdentity:
    """Read and validate one exact projected artifact identity."""
    supplied_root = Path(managed_path)
    if not supplied_root.is_absolute():
        raise PluginArtifactValidationError(
            f"projected plugin root must be absolute: {supplied_root}"
        )
    try:
        canonical_root = supplied_root.resolve(strict=True)
        root_stat = canonical_root.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin root is unavailable: {supplied_root}"
        ) from exc
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"projected plugin root cannot be read: {supplied_root}"
        ) from exc
    if supplied_root != canonical_root or not stat.S_ISDIR(root_stat.st_mode):
        raise PluginArtifactValidationError(
            f"projected plugin root must be a canonical directory: {supplied_root}"
        )

    canonical_manifest = projected_artifact_manifest_path(canonical_root)
    selected_manifest = Path(manifest_path)
    if selected_manifest != canonical_manifest:
        raise PluginArtifactValidationError(
            f"projected plugin manifest path is not canonical: {selected_manifest}"
        )
    try:
        manifest_stat = selected_manifest.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is missing: {selected_manifest}"
        ) from exc
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"projected plugin identity manifest cannot be read: {selected_manifest}"
        ) from exc
    if not stat.S_ISREG(manifest_stat.st_mode):
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is not a regular file: {selected_manifest}"
        )
    try:
        manifest = read_versioned_json(
            selected_manifest,
            PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            raise_io_errors=True,
            logger=logger,
        )
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"projected plugin identity manifest cannot be read: {selected_manifest}"
        ) from exc
    if manifest is None:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is unreadable: {selected_manifest}"
        )
    if frozenset(manifest) != _PROJECTION_ARTIFACT_MANIFEST_FIELDS:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest has unexpected fields: {selected_manifest}"
        )
    if manifest.get("artifact_kind") != PluginArtifactKind.PROJECTION.value:
        raise PluginArtifactValidationError(
            f"projected plugin artifact kind is invalid: {selected_manifest}"
        )
    semantic_key = manifest.get("semantic_key")
    if not isinstance(semantic_key, str) or semantic_key != expected_semantic_key:
        raise PluginArtifactValidationError(
            f"projected plugin semantic key mismatch: {selected_manifest}"
        )
    incarnation_id = manifest.get("incarnation_id")
    if not is_canonical_plugin_artifact_incarnation_id(incarnation_id):
        raise PluginArtifactValidationError(
            f"projected plugin incarnation is not canonical uuid4 hex: {selected_manifest}"
        )
    artifact_digest = manifest.get("artifact_digest")
    if not is_canonical_plugin_artifact_digest(artifact_digest):
        raise PluginArtifactValidationError(
            f"projected plugin digest is invalid: {selected_manifest}"
        )
    projection_version = manifest.get("projection_version")
    if type(projection_version) is not int or projection_version < 1:
        raise PluginArtifactValidationError(
            f"projected plugin version mismatch (invalid value): {selected_manifest}"
        )
    if expected_projection_version is not None and (
        projection_version != expected_projection_version
    ):
        raise PluginArtifactValidationError(
            f"projected plugin version mismatch: {selected_manifest}"
        )
    if not isinstance(manifest.get("skills"), dict):
        raise PluginArtifactValidationError(
            f"projected plugin skills manifest is invalid: {selected_manifest}"
        )
    observed_digest = projected_plugin_artifact_digest(canonical_root)
    if artifact_digest != observed_digest:
        raise PluginArtifactValidationError("projected plugin content digest mismatch")
    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        manifest_schema_version=PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=artifact_digest,
        managed_path=canonical_root,
        manifest_path=canonical_manifest,
    )


class ProjectedPluginRetirementOwner:
    """Exact-identity retirement owner for projected plugin generations."""

    def __init__(
        self,
        managed_root: Path,
        *,
        home: ManagedHome,
        active_key: str | None = None,
    ) -> None:
        self._retirement = PluginArtifactRetirementEngine(
            home=home,
            managed_root=managed_root,
            artifact_kind=PluginArtifactKind.PROJECTION,
            manifest_path=self.manifest_path,
            lease_path=self.lease_path,
            current_identity=self._current_identity,
            logger=logger,
            is_current=lambda path: active_key is not None and path.name == active_key,
        )

    @property
    def managed_root(self) -> Path:
        return self._retirement.managed_root

    def _contains(self, path: Path) -> bool:
        return self._retirement.contains(path)

    @staticmethod
    def manifest_path(managed_path: Path) -> Path:
        return projected_artifact_manifest_path(managed_path)

    @staticmethod
    def lease_path(managed_path: Path) -> Path:
        return projected_artifact_lease_path(managed_path)

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
    ) -> RetiringAppendResult | None:
        return self._retirement.enqueue_retirement(identity, not_before)

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...] | None:
        return self._retirement.cancel_obsolete_retirements(identity)

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

    def identity_for_path(self, managed_path: Path) -> PluginArtifactIdentity:
        """Validate and return the exact current identity at a managed path."""
        managed_path = Path(managed_path)
        if not self._contains(managed_path):
            raise PluginArtifactValidationError(
                f"projection is outside managed root: {managed_path}"
            )
        manifest_path = self.manifest_path(managed_path)
        identity = read_projected_plugin_identity(
            managed_path,
            manifest_path=manifest_path,
            expected_semantic_key=managed_path.name,
        )
        return identity

    def _current_identity(
        self,
        record: RetiringArtifactRecord,
    ) -> PluginArtifactIdentity:
        if record.manifest_path != self.manifest_path(record.managed_path):
            raise PluginArtifactValidationError(
                "projected retirement manifest path is not canonical"
            )
        if record.manifest_schema_version != PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION:
            raise PluginArtifactValidationError(
                "projected retirement manifest schema is unsupported"
            )
        return self.identity_for_path(record.managed_path)

    def try_reclaim(
        self,
        record: RetiringArtifactRecord,
        now: datetime,
    ) -> RetirementOutcome:
        return self._retirement.try_reclaim(record, now)


def _reconcile_projection_entry(
    entry: Path,
    *,
    root: Path,
    home: ManagedHome,
    owner: ProjectedPluginRetirementOwner,
    active_key: str,
    not_before: datetime,
) -> ProjectionReconcileDisposition:
    """Return exactly one durable or deferred outcome for a cache entry."""
    try:
        entry_class = classify_projection_entry(entry, active_key=active_key)
    except ValueError:
        return ProjectionReconcileDisposition.DEFERRED_UNCLASSIFIED

    if entry_class is ProjectionEntryClass.ACTIVE_ROOT:
        return ProjectionReconcileDisposition.SKIPPED_ACTIVE
    if entry_class is ProjectionEntryClass.RESIDUE_STAGING_ROOT:
        return _resume_projection_residue(
            entry,
            root=root,
            home=home,
            owner=owner,
            active_key=active_key,
        )
    if entry_class is not ProjectionEntryClass.PROJECTION_ROOT:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    if entry.parent != root:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED

    try:
        writer = ArtifactLease.acquire_exclusive(
            owner.lease_path(entry),
            timeout=0.0,
        )
    except ArtifactLeaseContention:
        return ProjectionReconcileDisposition.DEFERRED_CONTENDED
    except (OSError, RuntimeError):
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    try:
        with _InstallLock(home):
            try:
                identity = owner.identity_for_path(entry)
            except PluginArtifactValidationError:
                return _quarantine_invalid_projection(
                    entry,
                    root=root,
                    owner=owner,
                    active_key=active_key,
                )
            except PluginArtifactUnavailableError:
                return ProjectionReconcileDisposition.DEFERRED_UNAVAILABLE
            appended = owner.enqueue_retirement(identity, not_before)
            if appended is None:
                return ProjectionReconcileDisposition.DEFERRED_QUEUE_UNREADABLE
            if appended.created:
                return ProjectionReconcileDisposition.QUEUED_FOR_RETIREMENT
            return ProjectionReconcileDisposition.ALREADY_QUEUED
    except (OSError, RuntimeError):
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    finally:
        writer.close_preserving()


def _revalidate_projection_mutation_target(
    entry: Path,
    *,
    root: Path,
    active_key: str,
    owner: ProjectedPluginRetirementOwner,
) -> ProjectionReconcileDisposition | None:
    """Recheck write authority and shape immediately before a residue rename."""
    try:
        if not owner._contains(entry) or entry.parent != root or entry.name == active_key:
            return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
        exists = entry.exists() or entry.is_symlink()
        if not exists:
            return ProjectionReconcileDisposition.ALREADY_ABSENT
        if entry.is_symlink() or not entry.is_dir():
            return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    except OSError:
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    return None


def _quarantine_invalid_projection(
    entry: Path,
    *,
    root: Path,
    owner: ProjectedPluginRetirementOwner,
    active_key: str,
) -> ProjectionReconcileDisposition:
    """Atomically quarantine and remove one permanently invalid projection."""
    refusal = _revalidate_projection_mutation_target(
        entry,
        root=root,
        active_key=active_key,
        owner=owner,
    )
    if refusal is not None:
        return refusal

    staging = residue_staging_path(entry)
    manifest = owner.manifest_path(entry)
    try:
        staging_present = staging.exists() or staging.is_symlink()
        if staging_present:
            if staging.is_symlink() or not staging.is_dir():
                return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
            teardown_artifact_residue(staging=staging, manifest=manifest)
            refusal = _revalidate_projection_mutation_target(
                entry,
                root=root,
                active_key=active_key,
                owner=owner,
            )
            if refusal is not None:
                return refusal
        refusal = _revalidate_projection_mutation_target(
            entry,
            root=root,
            active_key=active_key,
            owner=owner,
        )
        if refusal is not None:
            return refusal
        quarantine_artifact_residue(
            managed_path=entry,
            staging=staging,
            manifest=manifest,
        )
    except OSError:
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    return ProjectionReconcileDisposition.RECONCILED


def _resume_projection_residue(
    entry: Path,
    *,
    root: Path,
    home: ManagedHome,
    owner: ProjectedPluginRetirementOwner,
    active_key: str,
) -> ProjectionReconcileDisposition:
    """Resume teardown for a deterministic residue staging directory."""
    match = _RESIDUE_STAGING_ROOT_RE.fullmatch(entry.name)
    if match is None:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    managed_path = root / match.group("key")
    if entry != residue_staging_path(managed_path) or managed_path.name == active_key:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    try:
        present = entry.exists() or entry.is_symlink()
        if not present:
            return ProjectionReconcileDisposition.ALREADY_ABSENT
        if entry.is_symlink() or not entry.is_dir() or not owner._contains(entry):
            return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
        writer = ArtifactLease.acquire_exclusive(
            owner.lease_path(managed_path),
            timeout=0.0,
        )
    except ArtifactLeaseContention:
        return ProjectionReconcileDisposition.DEFERRED_CONTENDED
    except (OSError, RuntimeError):
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    try:
        with _InstallLock(home):
            teardown_artifact_residue(
                staging=entry,
                manifest=owner.manifest_path(managed_path),
            )
    except (OSError, RuntimeError):
        return ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    finally:
        writer.close_preserving()
    return ProjectionReconcileDisposition.RESUMED


def _log_projection_reconcile(
    entry: Path,
    *,
    active_key: str,
    disposition: ProjectionReconcileDisposition,
) -> None:
    try:
        entry_class = classify_projection_entry(entry, active_key=active_key).value
    except ValueError:
        entry_class = "unclassified"
    event = "projected_plugin_reconcile"
    fields = {
        "path": str(entry),
        "entry_class": entry_class,
        "disposition": disposition.value,
    }
    if disposition in {
        ProjectionReconcileDisposition.DEFERRED_UNCLASSIFIED,
        ProjectionReconcileDisposition.DEFERRED_IO_ERROR,
        ProjectionReconcileDisposition.DEFERRED_UNAVAILABLE,
        ProjectionReconcileDisposition.DEFERRED_QUEUE_UNREADABLE,
    }:
        logger.warning(event, **fields)
    else:
        logger.debug(event, **fields)


def prune_stale_projections(
    projections_root: Path,
    *,
    home: ManagedHome,
    active_key: str,
) -> int:
    """Reconcile every cache entry and count newly queued stale identities."""
    root = Path(projections_root).expanduser().resolve(strict=False)
    owner = ProjectedPluginRetirementOwner(root, home=home, active_key=active_key)
    try:
        if not home.contains(owner.managed_root):
            logger.warning(
                "projected_plugin_reconcile",
                path=str(root),
                entry_class="projection_root",
                disposition=ProjectionReconcileDisposition.DEFERRED_IO_ERROR.value,
            )
            return 0
        with _InstallLock(home):
            if not root.is_dir():
                return 0
            entries = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    except (OSError, RuntimeError):
        logger.warning(
            "projected_plugin_reconcile",
            path=str(root),
            entry_class="projection_root",
            disposition=ProjectionReconcileDisposition.DEFERRED_IO_ERROR.value,
        )
        return 0

    created = 0
    not_before = datetime.now(UTC) + timedelta(hours=_PROJECTION_GRACE_HOURS)
    for entry in entries:
        disposition = _reconcile_projection_entry(
            entry,
            root=root,
            home=home,
            owner=owner,
            active_key=active_key,
            not_before=not_before,
        )
        _log_projection_reconcile(
            entry,
            active_key=active_key,
            disposition=disposition,
        )
        match disposition:
            case ProjectionReconcileDisposition.QUEUED_FOR_RETIREMENT:
                created += 1
            case (
                ProjectionReconcileDisposition.SKIPPED_ACTIVE
                | ProjectionReconcileDisposition.ALREADY_QUEUED
                | ProjectionReconcileDisposition.RECONCILED
                | ProjectionReconcileDisposition.RESUMED
                | ProjectionReconcileDisposition.DEFERRED_UNCLASSIFIED
                | ProjectionReconcileDisposition.DEFERRED_UNMANAGED
                | ProjectionReconcileDisposition.DEFERRED_CONTENDED
                | ProjectionReconcileDisposition.DEFERRED_IO_ERROR
                | ProjectionReconcileDisposition.DEFERRED_UNAVAILABLE
                | ProjectionReconcileDisposition.DEFERRED_QUEUE_UNREADABLE
                | ProjectionReconcileDisposition.ALREADY_ABSENT
            ):
                pass
            case _ as unreachable:
                assert_never(unreachable)
    return created
