"""Projected plugin artifact publication, validation, and lease authority."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from autoskillit.core import (
    SKILL_PROJECTION_VERSION,
    ArtifactLease,
    ArtifactLeaseContention,
    CodingAgentBackend,
    DirectInstall,
    EffectiveSkillCatalogAuthority,
    PluginArtifactContentionError,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactLifecycleLease,
    PluginArtifactPublicationError,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    PluginLoadMode,
    SkillAuthority,
    SkillExecutionRole,
    SkillSource,
    SkillSourceRef,
    _InstallLock,
    get_logger,
    log_plugin_artifact_lifecycle,
    new_plugin_artifact_incarnation_id,
    pkg_root,
    write_versioned_json,
)
from autoskillit.workspace._projected_artifact.materialization import (
    SkillProjectionContext,
    _copy_non_skill_plugin_assets,
    _default_base_branch,
    _direct_install_projection_context,
    _projection_skills_manifest,
    _replace_directory,
    _skill_sequence,
    materialize_agent_skill_tree,
    validate_sanitized_plugin_artifact,
)
from autoskillit.workspace._projection_cache import (
    PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ProjectedPluginRetirementOwner,
    ProjectionCacheKey,
    projected_artifact_lease_path,
    projected_artifact_manifest_path,
    projected_plugin_artifact_digest,
    prune_stale_projections,
    public_plugin_asset_digest,
    read_projected_plugin_identity,
)
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    _skill_info_from_frontmatter,
)

logger = get_logger(__name__)

__all__ = [
    "ProjectedPluginArtifactAuthority",
    "project_default_plugin_authority",
    "project_direct_install_authority",
]


@dataclass(frozen=True, slots=True)
class _ProjectedArtifactPlan:
    source_root: Path
    destination: Path
    manifest_path: Path
    lease_path: Path
    semantic_key: str
    catalog: EffectiveSkillCatalogAuthority
    validation_catalog: tuple[SkillAuthority, ...] | EffectiveSkillCatalogAuthority
    require_sources_within_root: bool
    context: SkillProjectionContext


@dataclass(frozen=True, slots=True)
class _StagedProjectedArtifact:
    root: Path
    manifest: Path
    identity: PluginArtifactIdentity


def _discard_staging_manifest(manifest: Path) -> None:
    """Best-effort cleanup that never replaces the active publication error."""
    try:
        if manifest.is_symlink() or manifest.is_file():
            manifest.unlink()
    except OSError as exc:
        logger.warning(
            "projected_plugin_staging_cleanup_failed",
            manifest_path=str(manifest),
            error=str(exc),
        )


def _stage_projected_plugin_artifact(
    plan: _ProjectedArtifactPlan,
) -> _StagedProjectedArtifact:
    """Build one complete, unpublished artifact incarnation."""
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f".{plan.destination.name}.plugin-",
            dir=plan.destination.parent,
        )
    )
    staging_manifest = plan.destination.parent / (
        f".{plan.destination.name}.manifest-{uuid.uuid4()}.json"
    )
    try:
        _copy_non_skill_plugin_assets(plan.source_root, staging_root)
        skill_infos = _skill_sequence(plan.catalog)
        documents = materialize_agent_skill_tree(
            staging_root / "skills",
            skill_infos,
            plan.context,
        )
        artifact_digest = projected_plugin_artifact_digest(staging_root)
        identity = PluginArtifactIdentity(
            semantic_key=plan.semantic_key,
            incarnation_id=new_plugin_artifact_incarnation_id(),
            manifest_schema_version=PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            artifact_digest=artifact_digest,
            managed_path=plan.destination,
            manifest_path=plan.manifest_path,
        )
        write_versioned_json(
            staging_manifest,
            {
                "schema_version": identity.manifest_schema_version,
                "artifact_kind": PluginArtifactKind.PROJECTION.value,
                "projection_version": plan.context.projection_version,
                "semantic_key": identity.semantic_key,
                "incarnation_id": identity.incarnation_id,
                "artifact_digest": identity.artifact_digest,
                "skills": _projection_skills_manifest(skill_infos, documents),
            },
            schema_version=identity.manifest_schema_version,
            strict_durability=True,
        )
        return _StagedProjectedArtifact(
            root=staging_root,
            manifest=staging_manifest,
            identity=identity,
        )
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        _discard_staging_manifest(staging_manifest)
        raise


def _publish_projected_plugin_root(
    staged: _StagedProjectedArtifact,
    destination: Path,
) -> None:
    """Atomically publish staged public bytes at their stable semantic path."""
    _replace_directory(staged.root, destination)


def _publish_projected_plugin_manifest(
    staged: _StagedProjectedArtifact,
    manifest_path: Path,
) -> None:
    """Publish identity last, so incomplete root publication is never trusted."""
    if manifest_path.exists() and not (manifest_path.is_file() or manifest_path.is_symlink()):
        raise PluginArtifactPublicationError(
            f"projected plugin manifest destination is not a file: {manifest_path}"
        )
    os.replace(staged.manifest, manifest_path)


def _manifest_identity(plan: _ProjectedArtifactPlan) -> PluginArtifactIdentity:
    return read_projected_plugin_identity(
        plan.destination,
        manifest_path=plan.manifest_path,
        expected_semantic_key=plan.semantic_key,
        expected_projection_version=plan.context.projection_version,
    )


def _validate_published_plugin_artifact(
    plan: _ProjectedArtifactPlan,
    *,
    expected_identity: PluginArtifactIdentity | None = None,
) -> PluginArtifactIdentity:
    """Validate both semantic content and exact physical incarnation."""
    identity = _manifest_identity(plan)
    errors = validate_sanitized_plugin_artifact(
        plan.source_root,
        plan.destination,
        plan.manifest_path,
        plan.validation_catalog,
        require_sources_within_root=plan.require_sources_within_root,
        manifest_schema_version=PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
    if errors:
        raise PluginArtifactValidationError(
            "projected plugin content validation failed: " + "; ".join(errors)
        )
    actual_digest = projected_plugin_artifact_digest(plan.destination)
    if actual_digest != identity.artifact_digest:
        raise PluginArtifactValidationError(
            "projected plugin digest mismatch: "
            f"expected {identity.artifact_digest}, got {actual_digest}"
        )
    if _manifest_identity(plan) != identity:
        raise PluginArtifactValidationError("projected plugin identity changed during validation")
    if expected_identity is not None and identity != expected_identity:
        raise PluginArtifactValidationError(
            "projected plugin incarnation changed before reader lease acquisition"
        )
    return identity


def _try_validate_published_plugin_artifact(
    plan: _ProjectedArtifactPlan,
) -> PluginArtifactIdentity | None:
    try:
        return _validate_published_plugin_artifact(plan)
    except PluginArtifactUnavailableError:
        raise
    except PluginArtifactValidationError:
        return None
    except Exception as exc:
        raise PluginArtifactValidationError(
            f"projected plugin validation failed: {plan.semantic_key}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ProjectedPluginArtifactAuthority:
    """Lazy owner of projected plugin publication and per-launch reader leases."""

    direct_install: DirectInstall
    projection_version: int = SKILL_PROJECTION_VERSION
    base_branch: str | None = None
    catalog: EffectiveSkillCatalogAuthority | None = None
    namespace_sources: Mapping[str, SkillSource] | None = None
    cwd: Path | None = None

    def __post_init__(self) -> None:
        if type(self.projection_version) is not int or self.projection_version < 1:
            raise ValueError("projection_version must be a positive integer")
        if self.namespace_sources is not None:
            object.__setattr__(
                self,
                "namespace_sources",
                MappingProxyType(dict(self.namespace_sources)),
            )
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd))

    def _plan(self, backend: CodingAgentBackend) -> _ProjectedArtifactPlan:
        source_root = self.direct_install.plugin_dir.resolve()
        bundled_root = source_root / "skills"
        if not source_root.is_dir():
            raise PluginArtifactPublicationError(
                f"direct plugin root does not exist: {source_root}"
            )
        if self.catalog is None and (not bundled_root.is_dir() or bundled_root.is_symlink()):
            raise PluginArtifactPublicationError(
                f"direct plugin has no canonical bundled skill root: {source_root}"
            )
        source_infos = (
            tuple()
            if self.catalog is not None
            else tuple(
                info
                for entry in sorted(bundled_root.iterdir(), key=lambda item: item.name)
                if (
                    not entry.is_symlink()
                    and entry.is_dir()
                    and not (entry / "SKILL.md").is_symlink()
                    and (entry / "SKILL.md").is_file()
                )
                and (
                    info := _skill_info_from_frontmatter(
                        entry.name,
                        SkillSource.BUNDLED,
                        entry / "SKILL.md",
                        source_ref=SkillSourceRef(
                            origin=SkillSource.BUNDLED,
                            logical_name=entry.name,
                            skill_path=entry / "SKILL.md",
                        ),
                    )
                ).execution_role
                is SkillExecutionRole.SESSION
            )
        )
        namespace_sources = (
            self.namespace_sources
            if self.namespace_sources is not None
            else (self.catalog.namespace_sources if self.catalog is not None else {})
        )
        catalog = self.catalog or EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in source_infos),
            execution_role=SkillExecutionRole.SESSION,
            namespace_sources=namespace_sources,
        )
        if self.catalog is not None and self.namespace_sources is not None:
            catalog = EffectiveSkillCatalog(
                skills=cast(tuple[SkillCatalogEntry, ...], tuple(self.catalog.skills)),
                execution_role=self.catalog.execution_role,
                namespace_sources=self.namespace_sources,
            )
        if not catalog.skills:
            raise PluginArtifactPublicationError(
                f"direct plugin has no bundled skills: {source_root}"
            )
        skill_identity = "\n".join(
            f"{info.name}:{info.canonical_digest}"
            for info in sorted(catalog.skills, key=lambda skill: skill.name)
        )
        namespace_identity = "\n".join(
            f"{name}:{source.value}" for name, source in sorted(catalog.namespace_sources.items())
        )
        semantic_key = ProjectionCacheKey(
            source_root=str(source_root),
            backend_name=backend.name,
            projection_version=self.projection_version,
            default_base_branch=_default_base_branch(self.base_branch),
            skill_identity=skill_identity,
            namespace_identity=namespace_identity,
            asset_digest=public_plugin_asset_digest(source_root),
        ).digest()
        projections_root = Path.home() / ".autoskillit" / "plugin-projections"
        destination = (projections_root / semantic_key).absolute()
        context = _direct_install_projection_context(
            cwd=self.cwd or Path.cwd(),
            project_root=None,
            catalog=catalog,
            backend=backend,
            destination=destination,
            default_base_branch=_default_base_branch(self.base_branch),
            projection_version=self.projection_version,
        )
        return _ProjectedArtifactPlan(
            source_root=source_root,
            destination=destination,
            manifest_path=projected_artifact_manifest_path(destination),
            lease_path=projected_artifact_lease_path(destination),
            semantic_key=semantic_key,
            catalog=catalog,
            validation_catalog=source_infos if source_infos else catalog,
            require_sources_within_root=bool(source_infos),
            context=context,
        )

    def acquire_launch_binding(
        self,
        *,
        backend: CodingAgentBackend,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding:
        if load_mode not in {
            PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            PluginLoadMode.PROJECTED_HOME,
        }:
            raise ValueError(
                f"projected plugin authority cannot bind load mode {load_mode.value!r}"
            )
        try:
            plan = self._plan(backend)
        except PluginArtifactPublicationError:
            raise
        except Exception as exc:
            raise PluginArtifactPublicationError(
                "projected plugin publication planning failed"
            ) from exc
        try:
            reader = ArtifactLease.acquire_shared(plan.lease_path)
        except Exception as exc:
            raise PluginArtifactPublicationError(
                f"projected plugin reader lease acquisition failed: {plan.semantic_key}"
            ) from exc
        try:
            identity = _try_validate_published_plugin_artifact(plan)
        except BaseException as primary_error:
            reader.close_preserving(primary_error)
            raise
        if identity is not None:
            return self._binding(load_mode, plan, identity, reader)
        reader.close()

        mutation_action = (
            "repair" if plan.destination.exists() or plan.manifest_path.exists() else "publish"
        )
        try:
            writer = ArtifactLease.acquire_exclusive(plan.lease_path, blocking=False)
        except ArtifactLeaseContention as exc:
            log_plugin_artifact_lifecycle(
                logger,
                action=mutation_action,
                outcome="deferred_contended",
                artifact_kind=PluginArtifactKind.PROJECTION.value,
                semantic_key=plan.semantic_key,
                incarnation="unknown",
                contention_detail=str(exc),
            )
            raise PluginArtifactContentionError(
                f"projected plugin mutation is contended: {plan.semantic_key}"
            ) from exc
        try:
            with _InstallLock():
                identity = _try_validate_published_plugin_artifact(plan)
                if identity is None:
                    plan.destination.parent.mkdir(parents=True, exist_ok=True)
                    staged: _StagedProjectedArtifact | None = None
                    try:
                        staged = _stage_projected_plugin_artifact(plan)
                        _publish_projected_plugin_root(staged, plan.destination)
                        _publish_projected_plugin_manifest(staged, plan.manifest_path)
                    except (
                        PluginArtifactPublicationError,
                        PluginArtifactValidationError,
                    ):
                        log_plugin_artifact_lifecycle(
                            logger,
                            action=mutation_action,
                            outcome="failed_validation",
                            artifact_kind=PluginArtifactKind.PROJECTION.value,
                            semantic_key=plan.semantic_key,
                            incarnation=(
                                staged.identity.incarnation_id if staged is not None else "unknown"
                            ),
                        )
                        raise
                    except Exception as exc:
                        log_plugin_artifact_lifecycle(
                            logger,
                            action=mutation_action,
                            outcome="failed_validation",
                            artifact_kind=PluginArtifactKind.PROJECTION.value,
                            semantic_key=plan.semantic_key,
                            incarnation=(
                                staged.identity.incarnation_id if staged is not None else "unknown"
                            ),
                        )
                        raise PluginArtifactPublicationError(
                            f"projected plugin publication failed: {plan.semantic_key}"
                        ) from exc
                    finally:
                        if staged is not None:
                            shutil.rmtree(staged.root, ignore_errors=True)
                            _discard_staging_manifest(staged.manifest)
                    assert staged is not None
                    try:
                        identity = _validate_published_plugin_artifact(
                            plan,
                            expected_identity=staged.identity,
                        )
                    except PluginArtifactValidationError:
                        log_plugin_artifact_lifecycle(
                            logger,
                            action=mutation_action,
                            outcome="failed_validation",
                            artifact_kind=PluginArtifactKind.PROJECTION.value,
                            semantic_key=plan.semantic_key,
                            incarnation=staged.identity.incarnation_id,
                        )
                        raise
                    log_plugin_artifact_lifecycle(
                        logger,
                        action=mutation_action,
                        outcome="succeeded",
                        artifact_kind=PluginArtifactKind.PROJECTION.value,
                        semantic_key=identity.semantic_key,
                        incarnation=identity.incarnation_id,
                    )
        except BaseException as primary_error:
            writer.close_preserving(primary_error)
            raise
        else:
            writer.close()

        try:
            reader = ArtifactLease.acquire_shared(plan.lease_path)
        except Exception as exc:
            raise PluginArtifactPublicationError(
                f"projected plugin reader lease acquisition failed: {plan.semantic_key}"
            ) from exc
        try:
            identity = _validate_published_plugin_artifact(
                plan,
                expected_identity=identity,
            )
        except PluginArtifactValidationError as primary_error:
            log_plugin_artifact_lifecycle(
                logger,
                action="acquire",
                outcome="failed_validation",
                artifact_kind=PluginArtifactKind.PROJECTION.value,
                semantic_key=plan.semantic_key,
                incarnation=identity.incarnation_id,
            )
            reader.close_preserving(primary_error)
            raise
        except BaseException as primary_error:
            reader.close_preserving(primary_error)
            raise
        return self._binding(load_mode, plan, identity, reader)

    @staticmethod
    def _binding(
        load_mode: PluginLoadMode,
        plan: _ProjectedArtifactPlan,
        identity: PluginArtifactIdentity,
        reader: ArtifactLease,
    ) -> PluginLaunchBinding:
        owner = ProjectedPluginRetirementOwner(plan.destination.parent)
        try:
            owner.cancel_obsolete_retirements(identity)
            prune_stale_projections(
                plan.destination.parent,
                active_key=plan.semantic_key,
            )
        except BaseException as primary_error:
            reader.close_preserving(primary_error)
            raise
        log_plugin_artifact_lifecycle(
            logger,
            action="acquire",
            outcome="succeeded",
            artifact_kind=PluginArtifactKind.PROJECTION.value,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
        )
        return PluginLaunchBinding(
            load_mode=load_mode,
            plugin_dir=(
                None if load_mode is PluginLoadMode.IMPLICIT_INSTALLED else plan.destination
            ),
            identity=identity,
            inherited_fds=reader.inherited_fds,
            _lease=PluginArtifactLifecycleLease(
                reader,
                logger=logger,
                artifact_kind=PluginArtifactKind.PROJECTION.value,
                semantic_key=identity.semantic_key,
                incarnation=identity.incarnation_id,
            ),
        )


def project_direct_install_authority(
    direct_install: DirectInstall,
    *,
    projection_version: int = SKILL_PROJECTION_VERSION,
    base_branch: str | None = None,
    catalog: EffectiveSkillCatalogAuthority | None = None,
    namespace_sources: Mapping[str, SkillSource] | None = None,
    cwd: Path | None = None,
) -> ProjectedPluginArtifactAuthority:
    return ProjectedPluginArtifactAuthority(
        direct_install=direct_install,
        projection_version=projection_version,
        base_branch=base_branch,
        catalog=catalog,
        namespace_sources=namespace_sources,
        cwd=cwd,
    )


def project_default_plugin_authority(
    *,
    projection_version: int = SKILL_PROJECTION_VERSION,
    base_branch: str | None = None,
    catalog: EffectiveSkillCatalogAuthority | None = None,
    namespace_sources: Mapping[str, SkillSource] | None = None,
    cwd: Path | None = None,
) -> ProjectedPluginArtifactAuthority:
    return project_direct_install_authority(
        DirectInstall(plugin_dir=pkg_root()),
        projection_version=projection_version,
        base_branch=base_branch,
        catalog=catalog,
        namespace_sources=namespace_sources,
        cwd=cwd,
    )
