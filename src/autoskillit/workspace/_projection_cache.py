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
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactRetirementEngine,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    _InstallLock,
    decode_versioned_json_bytes,
    directory_tree_digest,
    get_logger,
    is_canonical_plugin_artifact_digest,
    is_canonical_plugin_artifact_incarnation_id,
)

logger = get_logger(__name__)

__all__ = [
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ProjectionCacheKey",
    "ProjectedPluginRetirementOwner",
    "is_projected_asset",
    "iter_public_plugin_asset_files",
    "projected_artifact_lease_path",
    "projected_artifact_manifest_path",
    "projected_plugin_artifact_digest",
    "prune_stale_projections",
    "public_plugin_asset_digest",
    "read_projected_plugin_identity",
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
        "agents",
        "assets",
        "commands",
        "hooks",
        "recipes",
        "scripts",
        "settings.json",
    }
)


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
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"projected plugin artifact cannot be read for digest: {public_root}"
        ) from exc
    except ValueError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin artifact cannot be digested: {public_root}"
        ) from exc


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
        manifest_bytes = selected_manifest.read_bytes()
    except FileNotFoundError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is missing: {selected_manifest}"
        ) from exc
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"projected plugin identity manifest cannot be read: {selected_manifest}"
        ) from exc
    manifest = decode_versioned_json_bytes(
        manifest_bytes,
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
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
    """
    if entry.name in _CANONICAL_SKILL_DIRS:
        return False
    return not (top_level and entry.name not in _PUBLIC_PLUGIN_ASSET_NAMES)


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
    """
    digest = hashlib.sha256()
    for path in iter_public_plugin_asset_files(source_root):
        rel = path.relative_to(source_root).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
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
    namespace_identity: str
    asset_digest: str

    def digest(self) -> str:
        payload = "\0".join(
            (
                self.source_root,
                self.backend_name,
                str(self.projection_version),
                self.default_base_branch,
                self.skill_identity,
                self.namespace_identity,
                self.asset_digest,
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
            "EffectiveSkillDispatchContract, which is rebuilt per invocation and never "
            "cached, so two invocations differing only in cwd may safely share a projection."
        ),
        "project_root": (
            "Not byte-affecting, and constant: projection authority always passes "
            "project_root=None into the projection context."
        ),
        "skills": (
            "Covered by `skill_identity` (name + canonical digest, per skill) and "
            "`namespace_identity` (name -> source). The skills/ tree is regenerated from "
            "those contracts, so digesting the on-disk skill directories would be redundant."
        ),
        "skills_extended": (
            "Same as `skills`: canonical skill trees are never copied verbatim into a "
            "projection (_CANONICAL_SKILL_DIRS), only projected from their contracts."
        ),
    }
)


class ProjectedPluginRetirementOwner:
    """Exact-identity retirement owner for projected plugin generations."""

    def __init__(self, managed_root: Path) -> None:
        self._retirement = PluginArtifactRetirementEngine(
            managed_root=managed_root,
            artifact_kind=PluginArtifactKind.PROJECTION,
            manifest_path=self.manifest_path,
            lease_path=self.lease_path,
            current_identity=self._current_identity,
            logger=logger,
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
    ) -> RetiringAppendResult:
        return self._retirement.enqueue_retirement(identity, not_before)

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...]:
        return self._retirement.cancel_obsolete_retirements(identity)

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


def prune_stale_projections(
    projections_root: Path,
    *,
    active_key: str,
) -> int:
    """Queue exact stale incarnations without mutating reader-held artifacts."""
    root = Path(projections_root).expanduser().resolve(strict=False)
    owner = ProjectedPluginRetirementOwner(root)
    with _InstallLock():
        if not root.is_dir():
            return 0
        candidates = tuple(
            path
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.name != active_key
            and not path.name.startswith(".")
            and path.is_dir()
            and not path.is_symlink()
        )

    created = 0
    not_before = datetime.now(UTC) + timedelta(hours=_PROJECTION_GRACE_HOURS)
    for candidate in candidates:
        try:
            writer = ArtifactLease.acquire_exclusive(
                owner.lease_path(candidate),
                blocking=False,
            )
        except ArtifactLeaseContention:
            continue
        try:
            with _InstallLock():
                try:
                    identity = owner.identity_for_path(candidate)
                except PluginArtifactValidationError as exc:
                    logger.warning(
                        "projected_plugin_prune_validation_failed",
                        projection_path=str(candidate),
                        error=str(exc),
                    )
                    continue
                created += int(owner.enqueue_retirement(identity, not_before).created)
        finally:
            writer.close_preserving()
    return created
