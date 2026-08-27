"""What a plugin projection is made of, and when it goes stale.

Split out of ``skill_projection`` because staleness is its own concern: the
projection cache key used to cover only skill names and digests, so a release
that changed ``recipes/``, ``agents/``, or ``hooks/`` without touching a skill
produced an identical key and the previous release's assets were silently
reused. Keeping the asset inventory, the key record, and the retirement boundary in
one module is what stops those three from drifting apart again.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
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
    is_python_bytecode_path,
    read_versioned_json,
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

_CANONICAL_SKILL_DIRS = frozenset({"skills", "skills_extended"})
_PUBLIC_PLUGIN_ASSET_NAMES = frozenset(
    {
        ".claude-plugin",
        ".mcp.json",
        "_recipe_delivery_framing.py",
        "agents",
        "assets",
        "commands",
        "hooks",
        "recipes",
        "scripts",
        "settings.json",
    }
)

_PROJECTION_KEY_PATTERN = r"[0-9a-f]{24}"
_PROJECTION_ROOT_RE = re.compile(rf"{_PROJECTION_KEY_PATTERN}\Z")
_IDENTITY_SIDECAR_RE = re.compile(
    rf"\.(?P<key>{_PROJECTION_KEY_PATTERN})\.autoskillit-projection\.json\Z"
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
    DEFERRED_INVALID_IDENTITY = "deferred_invalid_identity"
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


def residue_staging_path(entry: Path) -> Path:
    """Return the deterministic quarantine path for a projection root."""
    suffix = hashlib.sha256(entry.name.encode()).hexdigest()[:16]
    return entry.parent / f".{entry.name}.autoskillit-residue-{suffix}"


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


def is_projected_asset(entry: Path, *, top_level: bool) -> bool:
    """Return True if *entry* is copied verbatim into a projection.

    The single predicate behind both the copier and the cache-key digest, so
    the two can never disagree about what a projection is made of.

    Bytecode artifacts (``__pycache__`` directories and ``*.pyc``/``*.pyo``
    files) are excluded at every depth — they are interpreter-generated
    ephemera that must never enter a published file set whose identity is
    bound by a content digest.
    """
    name = entry.name
    if is_python_bytecode_path(entry):
        return False
    if name in _CANONICAL_SKILL_DIRS:
        return False
    return not (top_level and name not in _PUBLIC_PLUGIN_ASSET_NAMES)


def iter_public_plugin_asset_files(source_root: Path, *, top_level: bool = True) -> Iterator[Path]:
    """Yield every regular file ``_copy_non_skill_plugin_assets`` would copy.

    Deliberately mirrors the copier's traversal via the shared
    ``_is_projected_asset`` predicate; ``test_asset_digest_covers_copied_files``
    asserts the two agree on a real projection.
    """
    if not source_root.is_dir():
        return
    for entry in sorted(source_root.iterdir(), key=lambda item: item.name):
        if not is_projected_asset(entry, top_level=top_level):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir():
            yield from iter_public_plugin_asset_files(entry, top_level=False)
        elif entry.is_file():
            yield entry


#: Renderer-owned manifest excluded from the source-asset digest.
_RENDERED_HOOKS_MANIFEST_RELPATH = "hooks/hooks.json"


def per_file_asset_digest(path: Path) -> str:
    """Content-only SHA-256 of one file, independent of its relpath or projection.

    Extracted from public_plugin_asset_digest's per-file loop (S3-1) so a
    content-addressed shared asset store can key on file bytes alone: 91 separate
    copies of the identical mermaid.min.js each recompute the SAME digest here and
    therefore hash to the same store entry, regardless of which projection or
    relative path they arrived at. Distinct from public_plugin_asset_digest
    (whole-set, path-qualified), authority.py's asset_digest/semantic_key
    (asset-set + skill/adaptation/namespace identities), and artifact_digest (over
    the staged *output* tree) -- none of the other three is per-file or
    path-independent.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def public_plugin_asset_digest(source_root: Path) -> str:
    """Digest every byte a projection copies out of *source_root*.

    This is what makes the projection cache key honest. ``identity`` and
    ``namespace_identity`` cover only skill names and digests, so without this
    a release that changes ``recipes/``, ``agents/``, ``hooks/``, or
    ``plugin.json`` — but no skill — produces the *same* key and the stale
    projection is reused. That is silent mixed-version execution, and it is the
    defect this whole module's source policy exists to prevent.

    A bare ``__version__`` would not do: under an editable install the version
    is static while the files change continuously, pinning a stale projection
    for an entire development cycle.

    ``hooks/hooks.json`` is excluded because its projected bytes come from the
    renderer (``render_hooks_json_text``), not from the source tree.  Its
    coverage is provided by ``rendered_hooks_digest`` in the cache key.
    """
    digest = hashlib.sha256()
    for path in iter_public_plugin_asset_files(source_root):
        rel = path.relative_to(source_root).as_posix()
        if rel == _RENDERED_HOOKS_MANIFEST_RELPATH:
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(per_file_asset_digest(path)))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectionCacheKey:
    """Every input that can change a projection's bytes, in one place.

    The key is derived from this record rather than a hand-concatenated string
    so a future input cannot be omitted by accident:
    ``test_cache_key_record_fields_are_keyed_or_excluded`` fails the build when
    a field appears here without being hashed, and when an entry in
    ``_PUBLIC_PLUGIN_ASSET_NAMES`` is neither digested nor excluded below.
    """

    source_root: str
    backend_name: str
    projection_version: int
    default_base_branch: str
    skill_identity: str
    adaptation_identity: str
    namespace_identity: str
    asset_digest: str
    rendered_hooks_digest: str

    def digest(self) -> str:
        payload = "\0".join(
            (
                self.source_root,
                self.backend_name,
                str(self.projection_version),
                self.default_base_branch,
                self.skill_identity,
                self.adaptation_identity,
                self.namespace_identity,
                self.asset_digest,
                self.rendered_hooks_digest,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


#: Projection inputs deliberately left out of the cache key, each with the
#: reason it cannot affect projected bytes. An input is either keyed or listed
#: here with a written rationale — a guard test permits no third option.
PROJECTION_CACHE_KEY_EXCLUSIONS: Mapping[str, str] = MappingProxyType(
    {
        "cwd": (
            "Not byte-affecting. The only substitutions bound by "
            "_direct_install_projection_context are {{AUTOSKILLIT_TEMP}} (process-wide), "
            "{{AUTOSKILLIT_SCRIPTS}} (derived from `destination`, which is derived from "
            "this key) and {{DEFAULT_BASE_BRANCH}} (keyed). `cwd` reaches only "
            "SkillProjectionBinding, which is rebuilt per invocation and never "
            "cached, so two invocations differing only in cwd may safely share a projection."
        ),
        "project_root": (
            "Not byte-affecting, and constant: projection authority always passes "
            "project_root=None into the projection context."
        ),
        "skills": (
            "Covered by `skill_identity` (name + canonical digest + sidecar digest, "
            "per skill) and `namespace_identity` (name -> source). The skills/ tree "
            "is regenerated from those contracts, and sidecar bytes are covered via "
            "skill_identity's sidecar-digest component, so digesting the on-disk skill "
            "directories would be redundant."
        ),
        "skills_extended": (
            "Same as `skills`: canonical skill trees are never copied verbatim into a "
            "projection (_CANONICAL_SKILL_DIRS), only projected from their contracts."
        ),
    }
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
    if entry_class is not ProjectionEntryClass.PROJECTION_ROOT:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    if entry.parent != root:
        return ProjectionReconcileDisposition.DEFERRED_UNMANAGED

    try:
        writer = ArtifactLease.acquire_exclusive(
            owner.lease_path(entry),
            blocking=False,
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
                return ProjectionReconcileDisposition.DEFERRED_INVALID_IDENTITY
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
        ProjectionReconcileDisposition.DEFERRED_INVALID_IDENTITY,
        ProjectionReconcileDisposition.DEFERRED_IO_ERROR,
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
    with _InstallLock(home):
        if not root.is_dir():
            return 0
        entries = tuple(sorted(root.iterdir(), key=lambda item: item.name))

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
                | ProjectionReconcileDisposition.DEFERRED_INVALID_IDENTITY
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
