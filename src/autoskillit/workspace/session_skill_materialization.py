"""Session-skill materialization transaction.

Single owner of the ordering-sensitive ``_materialize_session`` transaction
and the profile-projection helpers that share its ordering constraints.

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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypeAlias, TypedDict

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
    destination_location,
    get_logger,
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
    from autoskillit.core import CodingAgentBackend, CompiledSessionSkillCatalogAuthority

logger = get_logger(__name__)

_ExplorerBindingEnv: TypeAlias = Mapping[str, Mapping[str, str]]
_ExplorerBindingEnvFactory: TypeAlias = Callable[[Path], _ExplorerBindingEnv | None]


class _SessionSetupKwargs(TypedDict):
    parent_sandbox_mode: str
    execution_role: SkillExecutionRole
    agent_defs: NotRequired[tuple[AgentDef, ...]]
    explorer_binding_env: NotRequired[_ExplorerBindingEnv]


def _remove_generated_home_skill_entry(discovery_root: Path, skill: str) -> None:
    """Remove one exact generated-home discovery entry without following it."""
    root_location = destination_location(discovery_root)
    path = destination_location(discovery_root / skill)
    if path.parent != root_location or path.name != skill:
        raise SkillContractError(
            f"generated-home skill removal requires one exact child entry: {skill!r}"
        )
    if not os.path.lexists(path):
        return
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise RuntimeError(f"Refusing to remove invalid generated-home skill entry: {path}")
    if os.path.lexists(path):
        raise RuntimeError(f"Generated-home skill entry still exists after removal: {path}")


def _materialize_profile_skill_infos(
    generated_home: Path,
    infos: tuple[SkillInfo, ...],
    compilation: CompiledSessionSkillCatalog,
    backend: CodingAgentBackend,
    projection_context: SkillProjectionContextAuthority,
) -> CompiledSessionSkillCatalog:
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
        explorer_provisioning_eligible=projection_context.explorer_provisioning_eligible,
        projection_version=projection_context.projection_version,
    )
    materialize_agent_skill_tree(
        generated_home / backend.conventions.skills_subdir,
        compilation.catalog,
        profile_context,
    )
    return compilation


def materialize_profile_skills(
    generated_home: Path,
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
    )
    compilation = admission_compilation
    if finalized_native_roles is not None:
        compilation = _compile_reachable_profile_skill_catalog(
            admission_compilation,
            backend,
            finalized_native_roles,
        )
    return _materialize_profile_skill_infos(
        generated_home,
        infos,
        compilation,
        backend,
        projection_context,
    )


def _link_generated_home_skill_view(
    generated_home: Path,
    projected_skills: Path,
    *,
    skills_subdir: Path,
    execution_role: SkillExecutionRole,
) -> int:
    """Expose projected skills at a persistent backend's home discovery root."""
    discovery_root = generated_home / skills_subdir
    discovery_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for source in sorted(projected_skills.iterdir(), key=lambda entry: entry.name):
        skill_md = source / "SKILL.md"
        if source.is_symlink() or not source.is_dir() or not skill_md.is_file():
            raise SkillContractError(f"invalid projected session skill directory: {source}")
        target = discovery_root / source.name
        if os.path.lexists(target):
            if execution_role is SkillExecutionRole.ORCHESTRATOR:
                raise SkillContractError(f"orchestrator skill discovery collision at {target}")
            logger.debug(
                "generated_home_skill_collision_preserved",
                skill=source.name,
                target=str(target),
            )
            continue
        relative_source = Path(os.path.relpath(source, start=discovery_root))
        target.symlink_to(relative_source, target_is_directory=True)
        count += 1
    return count


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
        compilation = compile_session_skill_catalog(effective_catalog, backend)
        effective_catalog = compilation.catalog
        records = tuple(effective_catalog.skills)
    elif backend is not None and projection_context.invocation is not None:
        admitted_records: list[SkillAuthority] = []
        for record in records:
            plan = record.semantic_plan
            if plan is None:
                admitted_records.append(record)
                continue
            adaptation = backend.adapt_skill_semantics(plan)
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
            profile_compilation = _materialize_profile_skill_infos(
                generated_home,
                profile_skill_infos,
                profile_admission_compilation,
                backend,
                projection_context,
            )
        else:
            profile_compilation = _compile_reachable_profile_skill_catalog(
                profile_admission_compilation,
                backend,
                finalized_native_roles,
            )
            _materialize_profile_skill_infos(
                generated_home,
                profile_skill_infos,
                profile_compilation,
                backend,
                projection_context,
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
    if backend is not None and backend.capabilities.session_dir_persistent:
        linked = _link_generated_home_skill_view(
            generated_home,
            skills_base,
            skills_subdir=skills_subdir,
            execution_role=execution_role,
        )
        logger.debug("generated_home_skill_view_linked", count=linked)
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
    return ValidatedAddDir(path=str(add_dir)), records, unavailability_payload


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
    "_create_inert_rollout_paths",
    "_link_generated_home_skill_view",
    "_materialize_profile_skill_infos",
    "_materialize_session",
    "_remove_generated_home_skill_entry",
    "materialize_profile_skills",
]
