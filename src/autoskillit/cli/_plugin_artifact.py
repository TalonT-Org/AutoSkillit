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
) -> tuple[PluginArtifactAuthority | None, PluginLoadMode]:
    """Select authority only after the effective backend and load path are known."""
    from autoskillit.core import MARKETPLACE_PREFIX, detect_autoskillit_mcp_prefix

    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        return None, PluginLoadMode.NONE
    if not capabilities.plugin_install_capable and generated_home_available:
        return None, PluginLoadMode.GENERATED_HOME
    if capabilities.plugin_install_capable and (
        detect_autoskillit_mcp_prefix(capabilities) == MARKETPLACE_PREFIX
    ):
        return current_installed_plugin_authority(), PluginLoadMode.IMPLICIT_INSTALLED

    from autoskillit.workspace import project_default_plugin_authority

    authority = project_default_plugin_authority(
        base_branch=default_base_branch,
        catalog=skill_catalog,
        cwd=project_dir,
    )
    load_mode = (
        PluginLoadMode.EXPLICIT_PLUGIN_DIR
        if capabilities.plugin_install_capable
        else PluginLoadMode.PROJECTED_HOME
    )
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
        """Acquire a shared reader lease, then validate the exact incarnation."""
        del backend
        if load_mode is not PluginLoadMode.IMPLICIT_INSTALLED:
            raise PluginArtifactValidationError(
                "installed plugin authority requires implicit_installed load mode"
            )
        from autoskillit.workspace import (
            InstallStateLeaseMode,
            InstallStateSpec,
            verify_installed_plugin_artifact,
        )

        try:
            spec = InstallStateSpec.from_managed_root(
                self._root,
                self._semantic_key,
                require_registered_plugin=True,
                lease_mode=InstallStateLeaseMode.SHARED,
            )
        except ValueError as exc:
            raise PluginArtifactValidationError(str(exc)) from exc

        verification = verify_installed_plugin_artifact(spec)
        lease = verification.lease
        if verification.findings or verification.identity is None or lease is None:
            if lease is not None:
                lease.close()
            detail = "; ".join(finding.message for finding in verification.findings)
            raise PluginArtifactValidationError(
                detail or f"installed plugin identity is unavailable: {self._semantic_key}"
            )
        try:
            identity = verification.identity
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
                plugin_dir=None,
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
