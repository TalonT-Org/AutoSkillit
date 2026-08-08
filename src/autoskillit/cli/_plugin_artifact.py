"""Installed-plugin incarnation publication and launch-time authority."""

from __future__ import annotations

import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ArtifactLease,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactLifecycleLease,
    PluginArtifactPublicationError,
    PluginArtifactRetirementEngine,
    PluginArtifactRetirementOwner,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    PluginLoadMode,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheReadResult,
    RetiringCacheState,
    due_retiring_records,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_root,
    installed_plugin_semantic_key,
    log_plugin_artifact_lifecycle,
    migrate_retiring_cache_v1,
    read_installed_plugin_artifact_identity,
    read_retiring_cache,
    resolve_current_generation,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, PluginArtifactAuthority
    from autoskillit.workspace import EffectiveSkillCatalog


def current_installed_plugin_root() -> Path:
    """Return the lexical cache root created by the current install transaction."""
    from autoskillit import __version__

    return installed_plugin_artifact_root(Path.home(), "autoskillit", __version__)


def current_installed_plugin_authority() -> InstalledPluginArtifactAuthority:
    """Build the lazy authority for the currently installed release."""
    from autoskillit import __version__
    from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY

    return InstalledPluginArtifactAuthority(
        current_installed_plugin_root(),
        semantic_key=installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, __version__),
    )


def interactive_plugin_authority(
    *,
    backend: CodingAgentBackend,
    project_dir: Path,
    default_base_branch: str,
    skill_catalog: EffectiveSkillCatalog | None,
    generated_home_available: bool,
    retain_projection_source: bool = False,
) -> tuple[PluginArtifactAuthority | None, PluginLoadMode]:
    """Select authority only after the effective backend and load path are known."""
    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        if not retain_projection_source:
            return None, PluginLoadMode.NONE
        from autoskillit.workspace import project_default_plugin_authority

        return (
            project_default_plugin_authority(
                base_branch=default_base_branch,
                catalog=skill_catalog,
                cwd=project_dir,
            ),
            PluginLoadMode.NONE,
        )
    # All artifact-consuming backends use generation-store publication with
    # explicit --plugin-dir binding.  IMPLICIT_INSTALLED was retired in the
    # generation-keyed publication migration (#4480).
    from autoskillit.workspace import project_default_plugin_authority

    authority = project_default_plugin_authority(
        base_branch=default_base_branch,
        catalog=skill_catalog,
        cwd=project_dir,
    )
    if capabilities.plugin_install_capable:
        load_mode = PluginLoadMode.EXPLICIT_PLUGIN_DIR
    elif generated_home_available:
        load_mode = PluginLoadMode.GENERATED_HOME
    else:
        load_mode = PluginLoadMode.PROJECTED_HOME
    return authority, load_mode


def publish_installed_plugin_artifact(
    root: Path,
    *,
    semantic_key: str,
    _owned_exclusive_lease: ArtifactLease | None = None,
) -> PluginArtifactIdentity:
    """Persist a new exact identity after a successful plugin installation."""
    try:
        managed_path = _canonical_installed_root(root)
        manifest_path = installed_plugin_artifact_manifest_path(managed_path)
        if not manifest_path.is_absolute():
            raise PluginArtifactPublicationError(
                f"installed plugin manifest path is not absolute: {manifest_path}"
            )
        if _owned_exclusive_lease is not None:
            expected_lock = installed_plugin_artifact_lease_path(managed_path)
            if (
                _owned_exclusive_lease.closed
                or _owned_exclusive_lease.shared
                or _owned_exclusive_lease.path != expected_lock
            ):
                raise PluginArtifactPublicationError(
                    f"installed plugin publication lease does not own {expected_lock}"
                )
            return _publish_installed_plugin_artifact_locked(
                managed_path,
                semantic_key=semantic_key,
            )
        with ArtifactLease.acquire_exclusive(
            installed_plugin_artifact_lease_path(managed_path),
            blocking=True,
        ):
            return _publish_installed_plugin_artifact_locked(
                managed_path,
                semantic_key=semantic_key,
            )
    except PluginArtifactPublicationError:
        raise
    except Exception as exc:
        raise PluginArtifactPublicationError(
            f"failed to publish installed plugin artifact at {root}: {exc}"
        ) from exc


def _publish_installed_plugin_artifact_locked(
    managed_path: Path,
    *,
    semantic_key: str,
) -> PluginArtifactIdentity:
    """Publish identity while the caller owns the stable exclusive sidecar."""
    from autoskillit.workspace import write_installed_plugin_artifact_manifest_locked

    managed_path = _canonical_installed_root(managed_path)
    return write_installed_plugin_artifact_manifest_locked(
        managed_path,
        semantic_key=semantic_key,
        action="publish",
    )


class InstalledPluginArtifactAuthority:
    """Fail-closed authority for one installed plugin transaction identity."""

    def __init__(self, root: Path, *, semantic_key: str) -> None:
        self._root = Path(root)
        self._semantic_key = semantic_key

    def acquire_launch_binding(
        self,
        *,
        backend: CodingAgentBackend,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding:
        """Resolve current generation, acquire a shared reader lease, validate.

        If validation of the current generation fails, attempts a single
        self-heal republish from source before propagating the error.
        """
        del backend
        if not load_mode.consumes_artifact:
            raise PluginArtifactValidationError(
                f"installed plugin authority requires an artifact-consuming load mode, "
                f"got {load_mode.value!r}"
            )

        generation_dir = resolve_current_generation(
            Path.home(),
            "autoskillit",
            self._root.name,
        )
        if generation_dir is None:
            # No generation store yet — fall back to legacy installed root
            return self._acquire_from_root(self._root, load_mode)

        try:
            return self._acquire_from_root(generation_dir, load_mode)
        except PluginArtifactValidationError:
            healed = self._self_heal_republish()
            if healed is None:
                raise
            return self._acquire_from_root(healed, load_mode)

    def _self_heal_republish(self) -> Path | None:
        """Publish a replacement generation from source on validation failure.

        Returns the new generation directory on success, ``None`` if the
        republish itself fails. At most one self-heal per authority instance.
        """
        if getattr(self, "_self_healed", False):
            return None
        self._self_healed = True
        try:
            import json
            import tempfile

            from autoskillit import __version__
            from autoskillit.core import (
                _AUTOSKILLIT_PLUGIN_KEY,
                SkillExecutionRole,
                SkillSource,
                _InstallLock,
                atomic_write,
                pkg_root,
            )
            from autoskillit.hooks import generate_hooks_json
            from autoskillit.workspace import (
                DefaultSkillResolver,
                EffectiveSkillCatalog,
                SkillCatalogEntry,
                SkillProjectionContext,
                materialize_sanitized_plugin_root,
                publish_generation,
            )

            source_root = pkg_root()
            source_infos = tuple(
                s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
            )
            catalog = EffectiveSkillCatalog(
                skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
                execution_role=SkillExecutionRole.SESSION,
            )
            with tempfile.TemporaryDirectory(prefix="self-heal-") as staging:
                staging_root = Path(staging) / "content"
                materialize_sanitized_plugin_root(
                    source_root,
                    staging_root,
                    catalog,
                    SkillProjectionContext(cwd=source_root, catalog=catalog),
                )
                hooks_dir = staging_root / "hooks"
                hooks_dir.mkdir(parents=True, exist_ok=True)
                atomic_write(
                    hooks_dir / "hooks.json",
                    json.JSONEncoder(indent=2).encode(generate_hooks_json()) + "\n",
                )
                with _InstallLock():
                    identity = publish_generation(
                        home=Path.home(),
                        plugin_ref=_AUTOSKILLIT_PLUGIN_KEY,
                        version=__version__,
                        semantic_key=self._semantic_key,
                        source_root=staging_root,
                    )
            log_plugin_artifact_lifecycle(
                logger,
                action="self_heal_republish",
                outcome="succeeded",
                artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
                semantic_key=self._semantic_key,
                incarnation=identity.incarnation_id,
            )
            return identity.managed_path
        except Exception as exc:
            logger.warning(
                "self_heal_republish_failed: %s",
                exc,
                exc_info=True,
            )
            return None

    def _acquire_shared_lease_with_retry(
        self,
        managed_root: Path,
        *,
        max_retries: int = 3,
    ) -> ArtifactLease:
        """Acquire a shared lease on a generation, re-resolving on reclaim race."""
        lease_path = installed_plugin_artifact_lease_path(managed_root)
        last_error: OSError | None = None
        attempts = 0
        for _attempt in range(max_retries):
            attempts = _attempt + 1
            try:
                return ArtifactLease.acquire_existing_shared(lease_path)
            except OSError as exc:
                last_error = exc
                refreshed = resolve_current_generation(
                    Path.home(),
                    "autoskillit",
                    self._root.name,
                )
                if refreshed is not None and refreshed != managed_root:
                    managed_root = refreshed
                    lease_path = installed_plugin_artifact_lease_path(managed_root)
                    continue
                break
        raise PluginArtifactValidationError(
            f"installed plugin generation lease unavailable after "
            f"{attempts} attempt(s): {last_error}"
        ) from last_error

    def _acquire_from_root(
        self,
        managed_root: Path,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding:
        """Lease + validate one exact incarnation directory."""
        lease = self._acquire_shared_lease_with_retry(managed_root)
        try:
            identity = read_installed_plugin_artifact_identity(
                managed_root,
                expected_semantic_key=self._semantic_key,
                manifest_path=installed_plugin_artifact_manifest_path(managed_root),
            )
            InstalledPluginArtifactRetirementOwner(
                identity.managed_path.parent
            ).cancel_obsolete_retirements(identity)
            log_plugin_artifact_lifecycle(
                logger,
                action="acquire",
                outcome="succeeded",
                artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
                semantic_key=identity.semantic_key,
                incarnation=identity.incarnation_id,
            )
            return PluginLaunchBinding(
                load_mode=load_mode,
                plugin_dir=identity.managed_path,
                identity=identity,
                inherited_fds=lease.inherited_fds,
                _lease=PluginArtifactLifecycleLease(
                    lease,
                    logger=logger,
                    artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
                    semantic_key=identity.semantic_key,
                    incarnation=identity.incarnation_id,
                ),
            )
        except PluginArtifactValidationError as primary_error:
            log_plugin_artifact_lifecycle(
                logger,
                action="acquire",
                outcome="failed_validation",
                artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
                semantic_key=self._semantic_key,
                incarnation="unknown",
            )
            lease.close_preserving(primary_error)
            raise
        except BaseException as primary_error:
            lease.close_preserving(primary_error)
            raise


class InstalledPluginArtifactRetirementOwner:
    """Exact-identity retirement owner for AutoSkillit's installed cache."""

    def __init__(self, managed_root: Path) -> None:
        self._retirement = PluginArtifactRetirementEngine(
            managed_root=managed_root,
            artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
            manifest_path=installed_plugin_artifact_manifest_path,
            lease_path=installed_plugin_artifact_lease_path,
            current_identity=self._current_identity,
            logger=logger,
        )

    @property
    def managed_root(self) -> Path:
        return self._retirement.managed_root

    def _contains(self, path: Path) -> bool:
        return self._retirement.contains(path)

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
        *,
        on_persisted: Callable[[str], None] | None = None,
    ) -> RetiringAppendResult:
        return self._retirement.enqueue_retirement(
            identity,
            not_before,
            on_persisted=on_persisted,
        )

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...]:
        return self._retirement.cancel_obsolete_retirements(identity)

    @staticmethod
    def _current_identity(
        record: RetiringArtifactRecord,
    ) -> PluginArtifactIdentity:
        return _read_and_validate_identity(
            record.managed_path,
            expected_semantic_key=record.semantic_key,
        )

    def try_reclaim(
        self,
        record: RetiringArtifactRecord,
        now: datetime,
    ) -> RetirementOutcome:
        return self._retirement.try_reclaim(record, now)


class DefaultPluginRetirementCoordinator:
    """Cross-kind retirement dispatcher used by startup and explicit sweeps."""

    def __init__(
        self,
        *,
        projection_owner: PluginArtifactRetirementOwner,
        installed_owner: PluginArtifactRetirementOwner,
    ) -> None:
        self._owners = {
            PluginArtifactKind.PROJECTION: projection_owner,
            PluginArtifactKind.INSTALLED_PLUGIN: installed_owner,
        }
        self._managed_roots = {
            PluginArtifactKind.PROJECTION: projection_owner.managed_root,
            PluginArtifactKind.INSTALLED_PLUGIN: installed_owner.managed_root,
        }

    def migrate_legacy_cache(self) -> RetiringCacheReadResult:
        """Upgrade path-only retirement evidence before exact-v2 mutations."""
        state = read_retiring_cache()
        if state.state is RetiringCacheState.LEGACY_V1:
            return migrate_retiring_cache_v1(self._managed_roots)
        return state

    def sweep_due(self, now: datetime) -> tuple[RetirementOutcome, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("retirement coordinator time must be timezone-aware")
        now = now.astimezone(UTC)
        state = self.migrate_legacy_cache()
        if state.state is not RetiringCacheState.EXACT_V2:
            if state.state in {
                RetiringCacheState.CORRUPT,
                RetiringCacheState.UNSUPPORTED_FUTURE,
            }:
                import warnings

                warnings.warn(
                    f"retiring cache sweep skipped unsafe state {state.state.value}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            return ()
        outcomes = [RetirementOutcome.LEGACY_EVIDENCE for _item in state.legacy_evidence]
        for record in due_retiring_records(now):
            owner = self._owners[record.artifact_kind]
            outcomes.append(owner.try_reclaim(record, now))
        return tuple(outcomes)


def default_plugin_retirement_coordinator() -> DefaultPluginRetirementCoordinator:
    """Compose installed and projected owners without leaking CLI into server lifespan."""
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    projection_root = Path.home() / ".autoskillit" / "plugin-projections"
    installed_owner = InstalledPluginArtifactRetirementOwner(
        current_installed_plugin_root().parent
    )
    return DefaultPluginRetirementCoordinator(
        projection_owner=ProjectedPluginRetirementOwner(projection_root),
        installed_owner=installed_owner,
    )


def _read_and_validate_identity(
    managed_path: Path,
    *,
    expected_semantic_key: str,
) -> PluginArtifactIdentity:
    return read_installed_plugin_artifact_identity(
        managed_path,
        expected_semantic_key=expected_semantic_key,
        manifest_path=installed_plugin_artifact_manifest_path(managed_path),
    )


def _read_installed_plugin_identity(managed_path: Path) -> PluginArtifactIdentity:
    """Validate an installed identity whose semantic key is persisted on disk."""
    return read_installed_plugin_artifact_identity(
        managed_path,
        manifest_path=installed_plugin_artifact_manifest_path(managed_path),
    )


def _canonical_installed_root(root: Path) -> Path:
    supplied = Path(root)
    if not supplied.is_absolute():
        raise ValueError(f"installed plugin root must be absolute: {supplied}")
    resolved = supplied.resolve(strict=True)
    if supplied != resolved:
        raise ValueError(f"installed plugin root must already be canonical: {supplied}")
    root_stat = resolved.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"installed plugin root must be a directory: {resolved}")
    return resolved


__all__ = [
    "DefaultPluginRetirementCoordinator",
    "InstalledPluginArtifactAuthority",
    "InstalledPluginArtifactRetirementOwner",
    "current_installed_plugin_authority",
    "current_installed_plugin_root",
    "default_plugin_retirement_coordinator",
    "installed_plugin_artifact_lease_path",
    "installed_plugin_artifact_manifest_path",
    "installed_plugin_semantic_key",
    "interactive_plugin_authority",
    "publish_installed_plugin_artifact",
]
