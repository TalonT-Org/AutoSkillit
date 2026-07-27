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
import shutil
import uuid
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
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheState,
    _InstallLock,
    append_retiring_record,
    destination_location,
    get_logger,
    log_plugin_artifact_lifecycle,
    read_retiring_cache,
    read_versioned_json,
    remove_retiring_records,
)

logger = get_logger(__name__)

__all__ = [
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "ProjectionCacheKey",
    "ProjectedPluginRetirementOwner",
    "is_projected_asset",
    "iter_public_plugin_asset_files",
    "prune_stale_projections",
    "public_plugin_asset_digest",
]

#: Reserved grace window for lease-aware retirement.
_PROJECTION_GRACE_HOURS = 6
_ARTIFACT_KIND = PluginArtifactKind.PROJECTION.value

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
        self.managed_root = Path(managed_root).expanduser().resolve(strict=False)

    def _contains(self, path: Path) -> bool:
        try:
            location = destination_location(Path(path))
        except (OSError, ValueError):
            return False
        return location != self.managed_root and location.is_relative_to(self.managed_root)

    @staticmethod
    def manifest_path(managed_path: Path) -> Path:
        return managed_path.parent / f".{managed_path.name}.autoskillit-projection.json"

    @staticmethod
    def lease_path(managed_path: Path) -> Path:
        return managed_path.parent / ".artifact-leases" / f"{managed_path.name}.lock"

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
    ) -> RetiringAppendResult:
        if not self._contains(identity.managed_path):
            raise PluginArtifactValidationError(
                f"projection is outside managed root: {identity.managed_path}"
            )
        if identity.manifest_path != self.manifest_path(identity.managed_path):
            raise PluginArtifactValidationError(
                f"projection manifest path is not canonical: {identity.manifest_path}"
            )
        retired_at = datetime.now(UTC)
        result = append_retiring_record(
            RetiringArtifactRecord(
                record_id=uuid.uuid4().hex,
                artifact_kind=PluginArtifactKind.PROJECTION,
                semantic_key=identity.semantic_key,
                managed_path=identity.managed_path,
                manifest_path=identity.manifest_path,
                incarnation_id=identity.incarnation_id,
                manifest_schema_version=identity.manifest_schema_version,
                artifact_digest=identity.artifact_digest,
                retired_at=retired_at,
                not_before=not_before,
            )
        )
        log_plugin_artifact_lifecycle(
            logger,
            action="retire",
            outcome="succeeded",
            artifact_kind=_ARTIFACT_KIND,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
            not_before=not_before,
        )
        return result

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...]:
        state = read_retiring_cache()
        if state.state is not RetiringCacheState.EXACT_V2:
            return ()
        record_ids = tuple(
            record.record_id
            for record in state.records
            if record.artifact_kind is PluginArtifactKind.PROJECTION
            and record.managed_path == identity.managed_path
        ) + tuple(
            evidence.record_id
            for evidence in state.legacy_evidence
            if evidence.recognized_kind is PluginArtifactKind.PROJECTION
            and Path(evidence.path) == identity.managed_path
        )
        if not record_ids:
            return ()
        remove_retiring_records(record_ids)
        log_plugin_artifact_lifecycle(
            logger,
            action="cancel_retirement",
            outcome="succeeded",
            artifact_kind=_ARTIFACT_KIND,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
        )
        return record_ids

    def identity_for_path(self, managed_path: Path) -> PluginArtifactIdentity:
        """Validate and return the exact current identity at a managed path."""
        from autoskillit.workspace.skill_projection import (
            _PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            _projected_plugin_artifact_digest,
        )

        managed_path = Path(managed_path)
        if not self._contains(managed_path):
            raise PluginArtifactValidationError(
                f"projection is outside managed root: {managed_path}"
            )
        manifest_path = self.manifest_path(managed_path)
        manifest = read_versioned_json(
            manifest_path,
            _PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        )
        if manifest is None:
            raise PluginArtifactValidationError(
                f"projected retirement manifest is unreadable: {manifest_path}"
            )
        semantic_key = manifest.get("semantic_key")
        incarnation_id = manifest.get("incarnation_id")
        artifact_digest = manifest.get("artifact_digest")
        if semantic_key != managed_path.name:
            raise PluginArtifactValidationError(
                f"projected retirement semantic key mismatch: {manifest_path}"
            )
        if not isinstance(incarnation_id, str):
            raise PluginArtifactValidationError(
                f"projected retirement incarnation is missing: {manifest_path}"
            )
        try:
            parsed_incarnation = uuid.UUID(incarnation_id)
        except ValueError as exc:
            raise PluginArtifactValidationError(
                f"projected retirement incarnation is invalid: {manifest_path}"
            ) from exc
        if str(parsed_incarnation) != incarnation_id:
            raise PluginArtifactValidationError(
                f"projected retirement incarnation is not canonical: {manifest_path}"
            )
        if not isinstance(artifact_digest, str) or len(artifact_digest) != 64:
            raise PluginArtifactValidationError(
                f"projected retirement digest is invalid: {manifest_path}"
            )
        identity = PluginArtifactIdentity(
            semantic_key=semantic_key,
            incarnation_id=incarnation_id,
            manifest_schema_version=_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            artifact_digest=artifact_digest,
            managed_path=managed_path,
            manifest_path=manifest_path,
        )
        if _projected_plugin_artifact_digest(managed_path) != identity.artifact_digest:
            raise PluginArtifactValidationError("projected retirement digest mismatch")
        return identity

    def _current_identity(
        self,
        record: RetiringArtifactRecord,
    ) -> PluginArtifactIdentity:
        from autoskillit.workspace.skill_projection import (
            _PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        )

        if record.manifest_path != self.manifest_path(record.managed_path):
            raise PluginArtifactValidationError(
                "projected retirement manifest path is not canonical"
            )
        if record.manifest_schema_version != _PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION:
            raise PluginArtifactValidationError(
                "projected retirement manifest schema is unsupported"
            )
        return self.identity_for_path(record.managed_path)

    def try_reclaim(
        self,
        record: RetiringArtifactRecord,
        now: datetime,
    ) -> RetirementOutcome:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("projection retirement sweep time must be timezone-aware")
        now = now.astimezone(UTC)
        if record.artifact_kind is not PluginArtifactKind.PROJECTION:
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        if now < record.not_before:
            return RetirementOutcome.DEFERRED_NOT_DUE
        if not self._contains(record.managed_path):
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        try:
            writer = ArtifactLease.acquire_exclusive(
                self.lease_path(record.managed_path),
                blocking=False,
            )
        except ArtifactLeaseContention as exc:
            return self._log_reclaim(
                record,
                RetirementOutcome.DEFERRED_CONTENDED,
                detail=str(exc),
            )
        try:
            with _InstallLock():
                state = read_retiring_cache()
                queued = next(
                    (
                        current
                        for current in state.records
                        if current.record_id == record.record_id
                    ),
                    None,
                )
                if queued is None:
                    return RetirementOutcome.RECORD_REMOVED
                if queued != record:
                    return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
                if now < queued.not_before:
                    return RetirementOutcome.DEFERRED_NOT_DUE
                if not record.managed_path.exists() and not record.manifest_path.exists():
                    remove_retiring_records((record.record_id,))
                    return RetirementOutcome.RECORD_REMOVED
                try:
                    current = self._current_identity(record)
                except PluginArtifactValidationError:
                    remove_retiring_records((record.record_id,))
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.REJECTED_IDENTITY,
                        failed_validation=True,
                    )
                if current != record.identity:
                    remove_retiring_records((record.record_id,))
                    return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
                shutil.rmtree(record.managed_path)
                if record.manifest_path.is_file() or record.manifest_path.is_symlink():
                    record.manifest_path.unlink()
                remove_retiring_records((record.record_id,))
                return self._log_reclaim(record, RetirementOutcome.RECLAIMED)
        finally:
            writer.close()

    @staticmethod
    def _log_reclaim(
        record: RetiringArtifactRecord,
        outcome: RetirementOutcome,
        *,
        detail: str | None = None,
        failed_validation: bool = False,
    ) -> RetirementOutcome:
        event_outcome = {
            RetirementOutcome.RECLAIMED: "succeeded",
            RetirementOutcome.DEFERRED_CONTENDED: "deferred_contended",
            RetirementOutcome.REJECTED_IDENTITY: "rejected_identity",
        }[outcome]
        if failed_validation:
            event_outcome = "failed_validation"
        log_plugin_artifact_lifecycle(
            logger,
            action="reclaim",
            outcome=event_outcome,
            artifact_kind=_ARTIFACT_KIND,
            semantic_key=record.semantic_key,
            incarnation=record.incarnation_id,
            not_before=record.not_before,
            contention_detail=detail,
        )
        return outcome


def prune_stale_projections(
    projections_root: Path,
    *,
    active_key: str,
) -> int:
    """Queue exact stale incarnations without mutating reader-held artifacts."""
    from autoskillit.core import (
        ArtifactLease,
        ArtifactLeaseContention,
        PluginArtifactValidationError,
        _InstallLock,
    )

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
                except PluginArtifactValidationError:
                    continue
                created += int(owner.enqueue_retirement(identity, not_before).created)
        finally:
            writer.close()
    return created
