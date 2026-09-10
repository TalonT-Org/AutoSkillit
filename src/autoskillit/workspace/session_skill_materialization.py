"""Session-skill materialization transaction.

Single owner of the ordering-sensitive ``_materialize_session`` transaction,
the single catalog's profile merge, legacy discovery alias, and restore path.

The step order in ``_materialize_session`` is load-bearing:

- ``ensure_pre_launch`` runs before ``backend.setup_session_dir``, so a
  pre-launch failure aborts before any backend session state is created;
- records are pruned by ``finalized_native_roles`` after backend setup,
  never before;
- unavailability JSON is published before the ungated session tree;
- bundled-record filtering applies only for ``SkillExecutionRole.SESSION`` —
  other roles keep bundled records in the materialized tree.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypeAlias, TypedDict
from uuid import uuid4

from autoskillit.core import (
    SESSION_ADD_DIR_SUBDIR,
    AgentDef,
    CompiledSessionSkillCatalogAuthority,
    SkillAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillProjectionContextAuthority,
    SkillSemanticOperation,
    SkillSource,
    SkillUnavailabilityPayload,
    ValidatedAddDir,
    get_logger,
    strict_walk,
)
from autoskillit.workspace.session_skill_catalog import (
    CompiledSessionSkillCatalog,
    _canonical_skill_unavailability_payload,
    _compile_reachable_profile_skill_catalog,
    _merge_skill_unavailability_payloads,
    _profile_skill_catalog,
    _profile_skill_infos,
    _required_native_child_roles,
    _session_agent_definitions,
    compile_session_skill_catalog,
    write_skill_unavailability_metadata,
)
from autoskillit.workspace.skill_projection import (
    SkillProjectionContext,
    materialize_agent_skill_tree,
)
from autoskillit.workspace.skills import SkillInfo

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

logger = get_logger(__name__)

_ExplorerBindingEnv: TypeAlias = Mapping[str, Mapping[str, str]]
_ExplorerBindingEnvFactory: TypeAlias = Callable[[Path], _ExplorerBindingEnv | None]


class _SessionSetupKwargs(TypedDict):
    parent_sandbox_mode: str
    execution_role: SkillExecutionRole
    agent_defs: NotRequired[tuple[AgentDef, ...]]
    explorer_binding_env: NotRequired[_ExplorerBindingEnv]


def _materialize_profile_skill_infos(
    catalog_dir: Path,
    compilation: CompiledSessionSkillCatalog,
    backend: CodingAgentBackend,
    projection_context: SkillProjectionContextAuthority,
    *,
    execution_role: SkillExecutionRole,
) -> CompiledSessionSkillCatalog:
    """Project an admitted profile catalog into one existing session catalog."""
    for unavailable in compilation.unavailable:
        logger.warning(
            "profile_skill_unavailable",
            skill=unavailable.skill,
            backend=unavailable.backend,
            operation=unavailable.operation.value,
            diagnostic=unavailable.diagnostic,
        )
    profile_context = SkillProjectionContext(
        cwd=projection_context.cwd,
        project_root=projection_context.project_root,
        catalog=compilation.catalog,
        backend=backend,
        conventions=backend.conventions,
        substitutions=projection_context.substitutions,
        gating=False,
        namespace=projection_context.namespace,
        exploration_launch_context_ref=projection_context.exploration_launch_context_ref,
        resolved_exploration_profile=projection_context.resolved_exploration_profile,
        active_exploration_applicabilities=projection_context.active_exploration_applicabilities,
        parent_sandbox_mode=projection_context.parent_sandbox_mode,
        adaptation_context=projection_context.adaptation_context,
        managed_codex_route=projection_context.managed_codex_route,
        explorer_provisioning_eligible=projection_context.explorer_provisioning_eligible,
        projection_version=projection_context.projection_version,
    )
    staging = catalog_dir.parent / f".profile-projection-{uuid4().hex}"
    try:
        materialize_agent_skill_tree(staging, compilation.catalog, profile_context)
        _merge_profile_projection(staging, catalog_dir, execution_role)
    except BaseException as exc:
        try:
            _remove_profile_staging(staging)
        except BaseException as cleanup_exc:
            raise BaseExceptionGroup(
                "profile projection and staging cleanup failed",
                [exc, cleanup_exc],
            ) from None
        raise
    _remove_profile_staging(staging)
    return compilation


def _remove_profile_staging(staging: Path) -> None:
    """Remove a transaction-private profile projection directory completely."""
    if not os.path.lexists(staging):
        return
    if staging.is_symlink() or not staging.is_dir():
        raise RuntimeError(f"profile projection staging path is not a real directory: {staging}")
    shutil.rmtree(staging)
    if os.path.lexists(staging):
        raise RuntimeError(f"profile projection staging path remains after cleanup: {staging}")


def _merge_profile_projection(
    staging: Path,
    catalog_dir: Path,
    execution_role: SkillExecutionRole,
) -> None:
    """Merge profile projections after checking every collision before moving one."""
    if catalog_dir.is_symlink() or not catalog_dir.is_dir():
        raise SkillContractError(f"session skill catalog must be a real directory: {catalog_dir}")
    entries = sorted(staging.iterdir(), key=lambda entry: entry.name)
    for source in entries:
        skill_md = source / "SKILL.md"
        if (
            source.is_symlink()
            or not source.is_dir()
            or skill_md.is_symlink()
            or not skill_md.is_file()
        ):
            raise SkillContractError(f"invalid profile skill projection: {source}")
        target = catalog_dir / source.name
        if execution_role is SkillExecutionRole.ORCHESTRATOR and os.path.lexists(target):
            raise SkillContractError(f"orchestrator skill discovery collision at {target}")

    for source in entries:
        target = catalog_dir / source.name
        if os.path.lexists(target):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                raise SkillContractError(f"invalid session skill catalog entry: {target}")
        source.rename(target)


def materialize_profile_skills(
    catalog_dir: Path,
    profile_skills_source: Path,
    backend: CodingAgentBackend,
    projection_context: SkillProjectionContextAuthority,
    *,
    finalized_native_roles: frozenset[str] | None,
) -> CompiledSessionSkillCatalog:
    """Safely project the admitted skill catalog from one declared profile source."""
    infos = _profile_skill_infos(profile_skills_source)
    admission_compilation = compile_session_skill_catalog(
        _profile_skill_catalog(infos),
        backend,
        adaptation_context=projection_context.adaptation_context,
    )
    compilation = admission_compilation
    if finalized_native_roles is not None:
        compilation = _compile_reachable_profile_skill_catalog(
            admission_compilation,
            backend,
            finalized_native_roles,
        )
    return _materialize_profile_skill_infos(
        catalog_dir,
        compilation,
        backend,
        projection_context,
        execution_role=(
            projection_context.catalog.execution_role
            if projection_context.catalog is not None
            else SkillExecutionRole.SESSION
        ),
    )


def _alias_legacy_discovery_root(
    generated_home: Path,
    *,
    skills_subdir: Path,
) -> None:
    """Alias Codex's legacy discovery root to the one managed skill catalog."""
    discovery_root = generated_home / skills_subdir
    if os.path.lexists(discovery_root):
        raise SkillContractError(f"legacy discovery alias path already exists: {discovery_root}")
    target = Path(SESSION_ADD_DIR_SUBDIR) / skills_subdir
    os.symlink(str(target), discovery_root, target_is_directory=True)


def _materialize_session(
    generated_home: Path,
    records: tuple[SkillAuthority, ...],
    projection_context: SkillProjectionContextAuthority,
    *,
    skills_subdir: Path,
    compilation: CompiledSessionSkillCatalogAuthority | None = None,
    explorer_binding_env: _ExplorerBindingEnv | None = None,
    explorer_binding_env_factory: _ExplorerBindingEnvFactory | None = None,
) -> tuple[ValidatedAddDir, tuple[SkillAuthority, ...], SkillUnavailabilityPayload]:
    backend = projection_context.backend
    backend_name = backend.name if backend is not None else None
    add_dir = generated_home / SESSION_ADD_DIR_SUBDIR
    skills_base = add_dir / skills_subdir
    skills_base.mkdir(parents=True, exist_ok=True)

    effective_catalog = projection_context.catalog
    invocation_required_native_roles: set[str] = set()
    if compilation is not None:
        effective_catalog = compilation.catalog
        records = tuple(effective_catalog.skills)
    elif backend is not None and effective_catalog is not None:
        compilation = compile_session_skill_catalog(
            effective_catalog,
            backend,
            adaptation_context=projection_context.adaptation_context,
        )
        effective_catalog = compilation.catalog
        records = tuple(effective_catalog.skills)
    elif backend is not None and projection_context.invocation is not None:
        admitted_records: list[SkillAuthority] = []
        for record in records:
            plan = record.semantic_plan
            if plan is None:
                admitted_records.append(record)
                continue
            adaptation = backend.adapt_skill_semantics(plan, projection_context.adaptation_context)
            unsupported_operation = adaptation.validate_refusal_for(
                plan,
                backend=backend.name,
            )
            if unsupported_operation is not None:
                if record.name == projection_context.invocation.root.name:
                    adaptation.validate_for(plan, backend=backend.name)
                continue
            adaptation.validate_for(plan, backend=backend.name)
            invocation_required_native_roles.update(_required_native_child_roles(plan, adaptation))
            admitted_records.append(record)
        records = tuple(admitted_records)

    execution_role = (
        effective_catalog.execution_role
        if effective_catalog is not None
        else SkillExecutionRole.SESSION
    )
    profile_skills_source = (
        backend.conventions.profile_skills_source if backend is not None else None
    )
    profile_skill_infos: tuple[SkillInfo, ...] = ()
    profile_admission_compilation: CompiledSessionSkillCatalog | None = None
    if (
        backend is not None
        and execution_role is SkillExecutionRole.SESSION
        and profile_skills_source is not None
    ):
        profile_skill_infos = _profile_skill_infos(profile_skills_source)
        profile_admission_compilation = compile_session_skill_catalog(
            _profile_skill_catalog(profile_skill_infos),
            backend,
            adaptation_context=projection_context.adaptation_context,
        )

    if backend is not None and backend.capabilities.mcp_config_capable:
        readiness = backend.ensure_pre_launch(session_dir=generated_home)
        if readiness.errors:
            raise RuntimeError(f"Pre-launch check failed: {'; '.join(readiness.errors)}")
    if explorer_binding_env_factory is not None:
        explorer_binding_env = explorer_binding_env_factory(generated_home)
    finalized_native_roles: frozenset[str] | None = None
    if backend is not None:
        setup_kwargs: _SessionSetupKwargs = {
            "parent_sandbox_mode": projection_context.parent_sandbox_mode,
            "execution_role": execution_role,
        }
        if explorer_binding_env is not None:
            setup_kwargs["explorer_binding_env"] = explorer_binding_env
        if (
            compilation is not None
            or profile_admission_compilation is not None
            or projection_context.invocation is not None
        ):
            required_native_roles = set(invocation_required_native_roles)
            if compilation is not None:
                if not isinstance(compilation, CompiledSessionSkillCatalog):
                    raise SkillContractError(
                        "agent-definition provisioning requires a concrete session compilation"
                    )
                for targets in compilation.required_native_roles.values():
                    required_native_roles.update(targets)
            if profile_admission_compilation is not None:
                for targets in profile_admission_compilation.required_native_roles.values():
                    required_native_roles.update(targets)
            setup_kwargs["agent_defs"] = _session_agent_definitions(
                required_native_roles,
                explorer_binding_env,
            )
        finalized_native_roles = backend.setup_session_dir(generated_home, **setup_kwargs)
        attestation = (
            projection_context.adaptation_context.managed_join_attestation
            if projection_context.adaptation_context is not None
            else None
        )
        if backend.capabilities.managed_fixed_batch_route_capable and attestation is not None:
            configure_managed_home = getattr(backend, "configure_managed_session_dir", None)
            if not callable(configure_managed_home):
                raise SkillContractError("managed-route backend cannot configure a generated home")
            configure_managed_home(
                generated_home,
                attestation=attestation,
                route=projection_context.managed_codex_route or "parent",
            )

    if finalized_native_roles is not None and projection_context.invocation is not None:
        missing_invocation_roles = sorted(
            invocation_required_native_roles - finalized_native_roles
        )
        if missing_invocation_roles:
            raise SkillContractError(
                f"native child-spawn targets are unavailable: {missing_invocation_roles}"
            )

    if finalized_native_roles is not None and effective_catalog is not None:
        assert backend is not None
        if compilation is not None and not isinstance(compilation, CompiledSessionSkillCatalog):
            raise SkillContractError(
                "finalized native-role admission requires a concrete session compilation"
            )
        reachability_compilation = compile_session_skill_catalog(
            effective_catalog,
            backend,
            finalized_native_roles=finalized_native_roles,
            adaptation_context=projection_context.adaptation_context,
        )
        reachability_pruning = tuple(
            unavailable
            for unavailable in reachability_compilation.unavailable
            if unavailable.operation is SkillSemanticOperation.CHILD_SPAWN
        )
        if reachability_pruning:
            logger.error(
                "session_skill_native_role_unavailable",
                backend=backend.name,
                skills=tuple(item.skill for item in reachability_pruning),
                diagnostics=tuple(item.diagnostic for item in reachability_pruning),
                count=len(reachability_pruning),
            )
        prior_unavailable = compilation.unavailable if compilation is not None else ()
        compilation = CompiledSessionSkillCatalog(
            backend=backend.name,
            catalog=reachability_compilation.catalog,
            unavailable=tuple(
                sorted(
                    (*prior_unavailable, *reachability_compilation.unavailable),
                    key=lambda item: item.skill,
                )
            ),
            required_native_roles=reachability_compilation.required_native_roles,
        )
        effective_catalog = compilation.catalog
        records = tuple(effective_catalog.skills)

    ordinary_payload = (
        _merge_skill_unavailability_payloads(
            backend_name,
            compilation.unavailability_payload,
        )
        if compilation is not None
        else _canonical_skill_unavailability_payload(
            backend_name,
            (),
        )
    )
    profile_compilation: CompiledSessionSkillCatalog | None = None
    if backend is not None and profile_admission_compilation is not None:
        if finalized_native_roles is None:
            profile_compilation = profile_admission_compilation
        else:
            profile_compilation = _compile_reachable_profile_skill_catalog(
                profile_admission_compilation,
                backend,
                finalized_native_roles,
            )
    unavailability_payload = (
        _merge_skill_unavailability_payloads(
            backend_name,
            ordinary_payload,
            profile_compilation.unavailability_payload,
        )
        if profile_compilation is not None
        else ordinary_payload
    )

    write_skill_unavailability_metadata(
        add_dir,
        unavailability_payload=unavailability_payload,
    )

    ungated_context = SkillProjectionContext(
        cwd=projection_context.cwd,
        project_root=projection_context.project_root,
        catalog=effective_catalog,
        invocation=projection_context.invocation,
        backend=projection_context.backend,
        conventions=projection_context.conventions,
        substitutions=projection_context.substitutions,
        gating=False,
        namespace=projection_context.namespace,
        exploration_launch_context_ref=projection_context.exploration_launch_context_ref,
        resolved_exploration_profile=projection_context.resolved_exploration_profile,
        active_exploration_applicabilities=(projection_context.active_exploration_applicabilities),
        parent_sandbox_mode=projection_context.parent_sandbox_mode,
        adaptation_context=projection_context.adaptation_context,
        managed_codex_route=projection_context.managed_codex_route,
        explorer_provisioning_eligible=(
            explorer_binding_env is not None or projection_context.explorer_provisioning_eligible
        ),
        projection_version=projection_context.projection_version,
    )
    session_records = records
    if backend is not None and execution_role is SkillExecutionRole.SESSION:
        session_records = tuple(
            record for record in records if record.source is not SkillSource.BUNDLED
        )
    materialize_agent_skill_tree(skills_base, session_records, ungated_context)
    if backend is not None and profile_compilation is not None:
        _materialize_profile_skill_infos(
            skills_base,
            profile_compilation,
            backend,
            projection_context,
            execution_role=execution_role,
        )
    if backend is not None and backend.capabilities.session_dir_persistent:
        _alias_legacy_discovery_root(
            generated_home,
            skills_subdir=skills_subdir,
        )
        logger.debug("legacy_discovery_root_aliased", path=str(generated_home / skills_subdir))
    if backend is not None and backend.capabilities.session_dir_persistent:
        _create_inert_rollout_paths(generated_home, backend)
    if backend is not None:
        layout_errors = list(
            backend.validate_session_layout(
                generated_home,
                project_dir=projection_context.project_root or projection_context.cwd,
            )
        )
        if layout_errors:
            raise RuntimeError("Session layout validation failed: " + "; ".join(layout_errors))
    return (
        ValidatedAddDir(path=str(add_dir), session_home=str(generated_home)),
        records,
        unavailability_payload,
    )


def _restore_session(
    generated_home: Path,
    snapshot_dir: Path,
    projection_context: SkillProjectionContextAuthority,
    *,
    skills_subdir: Path,
) -> ValidatedAddDir:
    """Rebuild a generated backend home around one retained skill closure."""
    backend = projection_context.backend
    add_dir = generated_home / SESSION_ADD_DIR_SUBDIR
    catalog_dir = add_dir / skills_subdir
    _copy_restored_skill_catalog(snapshot_dir, catalog_dir, skills_subdir=skills_subdir)

    if backend is not None and backend.capabilities.mcp_config_capable:
        readiness = backend.ensure_pre_launch(session_dir=generated_home)
        if readiness.errors:
            raise RuntimeError(f"Pre-launch check failed: {'; '.join(readiness.errors)}")
    if backend is not None:
        backend.setup_session_dir(
            generated_home,
            parent_sandbox_mode=projection_context.parent_sandbox_mode,
            execution_role=SkillExecutionRole.SESSION,
        )
        if backend.capabilities.session_dir_persistent:
            _alias_legacy_discovery_root(generated_home, skills_subdir=skills_subdir)
            _create_inert_rollout_paths(generated_home, backend)
        layout_errors = list(
            backend.validate_session_layout(
                generated_home,
                project_dir=projection_context.project_root or projection_context.cwd,
            )
        )
        if layout_errors:
            raise RuntimeError("Session layout validation failed: " + "; ".join(layout_errors))
    return ValidatedAddDir(path=str(add_dir), session_home=str(generated_home))


def _copy_restored_skill_catalog(
    snapshot_dir: Path,
    catalog_dir: Path,
    *,
    skills_subdir: Path,
) -> None:
    """Validate a retained closure and copy it without accepting symlinks."""
    if snapshot_dir.is_symlink():
        raise ValueError(f"restored skill snapshot root must not be a symlink: {snapshot_dir}")
    if not snapshot_dir.is_dir():
        raise ValueError(f"restored skill snapshot root must be a real directory: {snapshot_dir}")
    _validate_restored_snapshot(snapshot_dir)
    source_catalog = snapshot_dir / skills_subdir
    if source_catalog.is_symlink() or not source_catalog.is_dir():
        raise ValueError(
            f"restored skill snapshot catalog must be a real directory: {source_catalog}"
        )
    _validate_restored_skill_catalog(source_catalog)
    if os.path.lexists(catalog_dir):
        raise RuntimeError(f"restored skill catalog path already exists: {catalog_dir}")
    catalog_dir.parent.mkdir(parents=True, exist_ok=True)
    catalog_dir.mkdir()
    for entry in strict_walk(source_catalog):
        relative_path = Path(entry.relative_path)
        destination = catalog_dir / relative_path
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {source_catalog / relative_path}"
            )
        if entry.kind == "d":
            destination.mkdir()
            continue
        if entry.kind != "f":
            raise ValueError(
                "restored skill snapshot contains an invalid entry: "
                f"{source_catalog / relative_path}"
            )
        _copy_restored_regular_file(entry.dir_fd, entry.name, destination)


def _validate_restored_snapshot(snapshot_dir: Path) -> None:
    """Reject links anywhere in a stored snapshot before inspecting its catalog."""
    for entry in strict_walk(snapshot_dir):
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {snapshot_dir / entry.relative_path}"
            )


def _validate_restored_skill_catalog(source_catalog: Path) -> None:
    """Require a complete regular-file catalog before creating its destination."""
    skill_names: set[str] = set()
    skill_documents: set[str] = set()
    for entry in strict_walk(source_catalog):
        relative_path = Path(entry.relative_path)
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {source_catalog / relative_path}"
            )
        if len(relative_path.parts) == 1:
            if entry.kind != "d" or relative_path.name.startswith("."):
                raise ValueError(
                    f"restored skill snapshot contains an invalid catalog entry: "
                    f"{source_catalog / relative_path}"
                )
            skill_names.add(relative_path.name)
        elif len(relative_path.parts) == 2 and relative_path.name == "SKILL.md":
            if entry.kind == "f":
                skill_documents.add(relative_path.parts[0])
    if not skill_names or skill_documents != skill_names:
        raise ValueError("restored skill snapshot entries must each contain a regular SKILL.md")


def _copy_restored_regular_file(source_dir_fd: int, source_name: str, destination: Path) -> None:
    """Copy one strict-walk file through a no-follow descriptor."""
    source_fd = os.open(
        source_name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=source_dir_fd,
    )
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError(f"restored skill snapshot contains a non-regular file: {source_name}")
        with os.fdopen(source_fd, "rb") as source:
            source_fd = -1
            with destination.open("xb") as target:
                shutil.copyfileobj(source, target)
    finally:
        if source_fd != -1:
            os.close(source_fd)


def _create_inert_rollout_paths(
    generated_home: Path,
    backend: CodingAgentBackend,
) -> None:
    """Create ``.inert-<name>`` rollout dirs and matching public symlinks for the backend."""
    configured = backend.capabilities.session_dir_symlinks
    for name in sorted(configured):
        if Path(name).name != name or name in {"", ".", ".."}:
            raise RuntimeError(f"Unsafe generated-home symlink declaration: {name!r}")
        target = generated_home / f".inert-{name}"
        public_path = generated_home / name
        if os.path.lexists(target) or os.path.lexists(public_path):
            raise RuntimeError(
                f"Backend setup created reserved generated-home rollout path: {public_path}"
            )
        target.mkdir(mode=0o700)
        target.chmod(0o700)
        public_path.symlink_to(target.name, target_is_directory=True)


__all__ = [
    "_ExplorerBindingEnv",
    "_ExplorerBindingEnvFactory",
    "_SessionSetupKwargs",
    "_alias_legacy_discovery_root",
    "_create_inert_rollout_paths",
    "_merge_profile_projection",
    "_materialize_profile_skill_infos",
    "_materialize_session",
    "_restore_session",
    "materialize_profile_skills",
]
