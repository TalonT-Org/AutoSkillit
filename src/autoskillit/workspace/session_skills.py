"""Per-session ephemeral skill directory management.

Provides three components:
  - resolve_ephemeral_root(): platform-aware writable dir discovery
  - SkillsDirectoryProvider: tier-aware skill content provider
  - DefaultSessionSkillManager: manages per-session ephemeral skill directories
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypeAlias, TypedDict, cast

from autoskillit.core import (
    SESSION_ADD_DIR_SUBDIR,
    ArtifactLease,
    ArtifactLeaseContention,
    ClaudeDirectoryConventions,
    CompiledSessionSkillCatalogAuthority,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    ManagedSessionHome,
    RepositoryProfileId,
    ResolvedSkillAuthority,
    SkillAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillFrontmatterAuthority,
    SkillProjectionContextAuthority,
    SkillResolver,
    SkillSemanticOperation,
    SkillSource,
    SkillSourceRef,
    ValidatedAddDir,
    get_logger,
    pkg_root,
    validate_skill_capability_roles,
    write_versioned_json,
)
from autoskillit.workspace.skill_projection import (
    SkillProjectionContext,
    materialize_agent_skill_tree,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillExclusion,
    SkillInfo,
    _skill_info_from_frontmatter,
    render_skill_invalidities,
)
from autoskillit.workspace.skills import (
    compute_skill_closure as compute_skill_closure,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]

logger = get_logger(__name__)

_SESSION_LEASES_SUBDIR = ".session-leases"
_SKILL_UNAVAILABILITY_SCHEMA_VERSION = 1

_ExplorerBindingEnv: TypeAlias = Mapping[str, Mapping[str, str]]
_ExplorerBindingEnvFactory: TypeAlias = Callable[[Path], _ExplorerBindingEnv | None]


class _SessionSetupKwargs(TypedDict):
    parent_sandbox_mode: str
    execution_role: SkillExecutionRole
    explorer_binding_env: NotRequired[_ExplorerBindingEnv]


def _raise_failures(message: str, failures: list[BaseException]) -> None:
    """Raise one failure unchanged, or preserve ordered failures as a group."""
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0]
    raise BaseExceptionGroup(message, failures)


def _remove_and_verify(path: Path) -> bool:
    """Remove a generated home and prove that no directory entry remains."""
    if not os.path.lexists(path):
        return False
    if path.is_symlink():
        raise RuntimeError(f"Refusing to recursively remove symlinked session home: {path}")
    shutil.rmtree(path)
    if os.path.lexists(path):
        raise RuntimeError(f"Session home still exists after removal: {path}")
    return True


def resolve_persistent_session_root(
    base_root: Path,
    backend: CodingAgentBackend,
) -> Path | None:
    """Resolve a backend-declared persistent generated-home root."""
    if not backend.capabilities.session_dir_persistent:
        return None
    subdir = backend.conventions.persistent_session_root_subdir
    if subdir is None:
        raise RuntimeError("Persistent backend has no generated-home root convention")
    if subdir.is_absolute() or ".." in subdir.parts:
        raise RuntimeError(f"Unsafe persistent generated-home root convention: {subdir}")
    return base_root / subdir


def resolve_persistent_session_roots(
    base_root: Path,
    backends: Iterable[CodingAgentBackend],
    *,
    required_backend_names: AbstractSet[str] = frozenset(),
) -> dict[str, Path]:
    """Resolve persistent generated-home roots for every persistent backend.

    A backend whose root convention is malformed is skipped unless its name is
    in required_backend_names, in which case the RuntimeError propagates —
    construction sites require their own load-bearing backend to be resolvable
    while deferring pinned-backend enforcement to preflight/doctor validation.
    """
    roots: dict[str, Path] = {}
    for backend in backends:
        try:
            root = resolve_persistent_session_root(base_root, backend)
        except RuntimeError:
            if backend.name in required_backend_names:
                raise
            logger.warning(
                "persistent_root_unresolvable_for_backend",
                backend=backend.name,
                exc_info=True,
            )
            continue
        if root is not None:
            roots[backend.name] = root
    return roots


@dataclass(slots=True)
class _SessionLease:
    """Workspace-owned external lease for a removable generated home."""

    lease: ArtifactLease

    @property
    def path(self) -> Path:
        return self.lease.path

    @property
    def fd(self) -> int | None:
        return self.lease.fd

    @classmethod
    def acquire(
        cls,
        lock_path: Path,
        *,
        blocking: bool,
    ) -> _SessionLease | None:
        try:
            lease = ArtifactLease.acquire_exclusive(
                lock_path,
                blocking=blocking,
            )
        except ArtifactLeaseContention:
            return None
        except BaseException as exc:
            logger.error("session_lease_acquisition_failed", exc_info=True)
            raise exc
        return cls(lease=lease)

    def release(self) -> None:
        try:
            self.lease.close()
        except BaseException:
            logger.error("session_lease_close_failed", exc_info=True)
            raise


@dataclass(frozen=True, slots=True)
class _InitializedSession:
    generated_home: Path
    skills_dir: ValidatedAddDir
    skills_subdir: Path
    lease: _SessionLease | None


@dataclass(frozen=True, slots=True)
class SkillUnavailableMetadata:
    """Deterministic SESSION omission with supplemental backend detail."""

    skill: str
    backend: str
    operation: SkillSemanticOperation
    diagnostic: str

    def to_payload(self) -> dict[str, str]:
        return {
            "skill": self.skill,
            "backend": self.backend,
            "operation": self.operation.value,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class CompiledSessionSkillCatalog:
    backend: str
    catalog: EffectiveSkillCatalog
    unavailable: tuple[SkillUnavailableMetadata, ...]

    @property
    def unavailability_payload(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "unavailable": tuple(item.to_payload() for item in self.unavailable),
        }


def compile_session_skill_catalog(
    catalog: EffectiveSkillCatalogAuthority,
    backend: CodingAgentBackend,
) -> CompiledSessionSkillCatalog:
    """Publish only skills whose mandatory semantics adapt on the selected backend."""
    supported: list[SkillCatalogEntry] = []
    unavailable: list[SkillUnavailableMetadata] = []
    for skill in catalog.skills:
        plan = skill.semantic_plan
        if plan is None:
            supported.append(cast(SkillCatalogEntry, skill))
            continue
        adaptation = backend.adapt_skill_semantics(plan)
        unsupported_operation = adaptation.validate_refusal_for(
            plan,
            backend=backend.name,
        )
        if unsupported_operation is not None:
            unavailable.append(
                SkillUnavailableMetadata(
                    skill=skill.name,
                    backend=backend.name,
                    operation=unsupported_operation,
                    diagnostic=adaptation.diagnostic or "unsupported skill semantics",
                )
            )
            continue
        adaptation.validate_for(plan, backend=backend.name)
        supported.append(cast(SkillCatalogEntry, skill))
    filtered_names = {skill.name for skill in supported}
    namespace_sources = {
        name: source
        for name, source in catalog.namespace_sources.items()
        if name in filtered_names
    }
    return CompiledSessionSkillCatalog(
        backend=backend.name,
        catalog=EffectiveSkillCatalog(
            skills=tuple(supported),
            execution_role=catalog.execution_role,
            namespace_sources=namespace_sources,
            exclusions=cast(tuple[SkillExclusion, ...], tuple(catalog.exclusions)),
        ),
        unavailable=tuple(sorted(unavailable, key=lambda item: item.skill)),
    )


def write_skill_unavailability_metadata(
    add_dir: Path,
    *,
    compilation: CompiledSessionSkillCatalogAuthority | None,
    backend: str | None = None,
) -> None:
    """Publish deterministic machine-readable SESSION catalog omissions."""
    metadata = (
        dict(compilation.unavailability_payload)
        if compilation is not None
        else {"backend": backend, "unavailable": ()}
    )
    write_versioned_json(
        add_dir / "skill-unavailability.json",
        metadata,
        schema_version=_SKILL_UNAVAILABILITY_SCHEMA_VERSION,
    )


def _codex_profile_skill_infos() -> tuple[SkillInfo, ...]:
    profile_skills_root = Path.home() / ".codex" / "skills"
    if not profile_skills_root.is_dir():
        return ()
    result: list[SkillInfo] = []
    for entry in sorted(profile_skills_root.iterdir(), key=lambda item: item.name):
        skill_md = entry / "SKILL.md"
        if (
            entry.is_symlink()
            or skill_md.is_symlink()
            or not entry.is_dir()
            or not skill_md.is_file()
        ):
            continue
        info = _skill_info_from_frontmatter(
            entry.name,
            SkillSource.THIRD_PARTY,
            skill_md,
            source_ref=SkillSourceRef(
                origin=SkillSource.THIRD_PARTY,
                logical_name=entry.name,
                skill_path=skill_md,
                search_dir=str(profile_skills_root),
            ),
        )
        if info.invalidities or info.execution_role is not SkillExecutionRole.SESSION:
            logger.warning(
                "codex_profile_skill_contract_rejected",
                skill=entry.name,
                reason=(
                    render_skill_invalidities(info.invalidities)
                    if info.invalidities
                    else "non-session execution role"
                ),
            )
            continue
        result.append(info)
    return tuple(result)


def _materialize_codex_profile_skill_infos(
    session_dir: Path,
    backend: CodingAgentBackend,
) -> tuple[SkillInfo, ...]:
    profile_skills_root = Path.home() / ".codex" / "skills"
    if not profile_skills_root.is_dir():
        return ()
    infos = _codex_profile_skill_infos()
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    catalog = compile_session_skill_catalog(catalog, backend).catalog
    materialize_agent_skill_tree(
        session_dir / backend.conventions.skills_subdir,
        catalog,
        SkillProjectionContext(
            cwd=Path.cwd().resolve(),
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions,
        ),
    )
    published_names = {skill.name for skill in catalog.skills}
    return tuple(info for info in infos if info.name in published_names)


def materialize_codex_profile_skills(
    session_dir: Path,
    backend: CodingAgentBackend,
) -> int:
    """Project profile skills into a Codex session without exposing machine fields."""
    return len(_materialize_codex_profile_skill_infos(session_dir, backend))


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


def resolve_ephemeral_root() -> Path:
    """Return a writable ephemeral root directory for session skill dirs.

    Tries /dev/shm/autoskillit-sessions (Linux tmpfs), then
    /tmp/autoskillit-sessions, then tempfile.gettempdir().
    Creates the chosen directory if it does not exist.
    """
    candidates = _CANDIDATE_ROOTS + [Path(tempfile.gettempdir())]
    for base in candidates:
        target = base / "autoskillit-sessions"
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_probe"
            probe.touch()
            probe.unlink()
            return target
        except (OSError, PermissionError):
            continue
    raise RuntimeError("No writable ephemeral root found for session skill dirs")


def _parse_write_paths(parsed: SkillFrontmatterAuthority) -> list[str]:
    """Extract write paths from the contract's single frontmatter parse."""
    if not parsed.is_valid or parsed.data is None:
        return []
    raw = parsed.data.get("write_paths", [])
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if p and isinstance(p, str)]


def resolve_closure_write_dirs(
    closure: tuple[ResolvedSkillAuthority, ...],
    cwd: str,
    existing: list[Path] | None = None,
) -> list[Path]:
    """Resolve write_paths from an exact effective closure into absolute Paths.

    Substitutes ``{{AUTOSKILLIT_TEMP}}`` with ``cwd/.autoskillit/temp`` and
    returns deduplicated resolved Paths ready to extend ``write_watch_dirs``.
    Paths already present in ``existing`` are excluded from the result.
    """
    raw_paths = tuple(
        write_path
        for info in closure
        if info.frontmatter is not None
        for write_path in _parse_write_paths(info.frontmatter)
    )
    if not raw_paths:
        return []
    temp_prefix = os.path.join(cwd, ".autoskillit", "temp")
    seen: set[Path] = set(existing) if existing else set()
    result: list[Path] = []
    for rwp in raw_paths:
        resolved = Path(rwp.replace("{{AUTOSKILLIT_TEMP}}", temp_prefix))
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


class SkillsDirectoryProvider:
    """Provides bundled skill content with tier-aware frontmatter injection."""

    def __init__(
        self,
        temp_dir_relpath: str = ".autoskillit/temp",
        default_base_branch: str = "main",
    ) -> None:
        if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
            raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
        if "\n" in default_base_branch or ": " in default_base_branch:
            raise ValueError(f"default_base_branch is YAML-unsafe: {default_base_branch!r}")
        self._resolver = DefaultSkillResolver()
        self._temp_dir_relpath = temp_dir_relpath
        self._default_base_branch = default_base_branch

    @property
    def resolver(self) -> SkillResolver:
        """Expose the underlying SkillResolver for target skill resolution."""
        return self._resolver

    def list_skills(self) -> list[SkillInfo]:
        """List all public bundled skills."""
        return self._resolver.list_all()

    def get_skill_content(
        self,
        skill_info: SkillInfo,
        *,
        cwd: Path,
        gated: bool = True,
    ) -> str:
        """Project already-resolved SKILL.md content with optional gating.

        - gated=True  → ensure disable-model-invocation: true is present
          (used only by the activate path — init_session omits gated skills entirely)
        - gated=False → return unmodified content (cook session or Tier 1 skills)

        Substitutes ``{{AUTOSKILLIT_TEMP}}`` with the configured temp dir relpath.
        Tier 1 skills (which contain no placeholder) are unaffected.
        """
        # Explicit pkg_root() — get_skill_content has no plugin artifact binding
        # (it serves the activate/init paths that read from the dev checkout).
        return self.project_skill_info(
            skill_info,
            cwd=cwd,
            gating=True if gated else None,
            durable_scripts_root=pkg_root(),
        )

    def projection_context(
        self,
        skill_info: SkillInfo,
        cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
    ) -> SkillProjectionContext:
        """Build the shared execution-local projection context.

        ``durable_scripts_root`` is required — every caller must supply the
        root whose lifetime exceeds the session's (no implicit ``pkg_root()``
        default, which would bake a venv-relative path into projected documents).
        """
        catalog = EffectiveSkillCatalog(
            skills=(SkillCatalogEntry.from_skill_info(skill_info),),
            execution_role=skill_info.execution_role or SkillExecutionRole.SESSION,
        )
        return self.catalog_projection_context(
            catalog,
            cwd,
            gating=gating,
            backend=backend,
            durable_scripts_root=durable_scripts_root,
        )

    def catalog_projection_context(
        self,
        catalog: EffectiveSkillCatalog,
        cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
        resolved_exploration_profile: RepositoryProfileId | None = None,
    ) -> SkillProjectionContext:
        """Build one projection context bound to a resolved path-free catalog.

        ``durable_scripts_root`` is the root a projected document's
        ``{{AUTOSKILLIT_SCRIPTS}}`` placeholder resolves against — it must
        never have a shorter lifetime than the session consuming the
        projected document. Required — no implicit default.  Callers that hold
        a retained plugin-cache incarnation (durable across a mid-session
        ``autoskillit update`` via retire-don't-delete) must pass the binding's
        ``identity.managed_path``; callers operating from the dev checkout pass
        ``pkg_root()`` explicitly.
        """
        scripts_root = durable_scripts_root
        return SkillProjectionContext(
            cwd=cwd,
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions if backend is not None else None,
            resolved_exploration_profile=resolved_exploration_profile,
            substitutions={
                "{{AUTOSKILLIT_TEMP}}": self._temp_dir_relpath,
                "{{AUTOSKILLIT_SCRIPTS}}": str(scripts_root / "recipes" / "scripts"),
                "{{DEFAULT_BASE_BRANCH}}": self._default_base_branch,
            },
            gating=gating,
        )

    def project_skill_info(
        self,
        skill_info: SkillInfo,
        *,
        cwd: Path,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
    ) -> str:
        """Project one already-resolved exact skill contract."""
        context = self.projection_context(
            skill_info,
            cwd,
            gating=gating,
            backend=backend,
            durable_scripts_root=durable_scripts_root,
        )
        return project_agent_skill_document(context.skills[0], context).content


def default_skill_resolver() -> DefaultSkillResolver:
    """Construct the standard resolver for non-injected session dispatch."""
    return DefaultSkillResolver()


class DefaultSessionSkillManager:
    """Manages per-session ephemeral skill directories."""

    def __init__(
        self,
        provider: SkillsDirectoryProvider,
        ephemeral_root: Path,
        *,
        persistent_roots: Mapping[str, Path] | None = None,
    ) -> None:
        self._provider = provider
        self._root = ephemeral_root
        self._persistent_roots: dict[str, Path] = dict(persistent_roots or {})
        self._session_roots: dict[str, Path] = {}
        self._session_leases: dict[str, _SessionLease] = {}
        self._session_skills_subdirs: dict[str, Path] = {}
        self._session_skill_infos: dict[str, dict[str, SkillAuthority]] = {}

    def materialize_invocation(
        self,
        session_id: str,
        invocation: EffectiveSkillInvocationAuthority,
        projection_context: SkillProjectionContextAuthority,
        *,
        explorer_binding_env: _ExplorerBindingEnv | None = None,
        explorer_binding_env_factory: _ExplorerBindingEnvFactory | None = None,
    ) -> ValidatedAddDir:
        """Write only a prevalidated closure from its captured canonical content."""
        self._validate_session_id(session_id)
        if not invocation.closure or invocation.root not in invocation.closure:
            raise ValueError("Effective invocation closure must contain its root")
        if invocation.execution_role is not SkillExecutionRole.SESSION:
            raise SkillContractError("L1 materialization requires an exact SESSION invocation")
        for member in invocation.closure:
            if member.invalidities:
                raise SkillContractError(
                    f"invalid materialization contract for {member.name!r}: "
                    f"{render_skill_invalidities(member.invalidities)}"
                )
            if member.execution_role is not SkillExecutionRole.SESSION:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"L1 materialization requires SESSION members; {member.name!r} is {actual}"
                )
            validate_skill_capability_roles(
                member.uses_capabilities,
                member.execution_role,
            )
        if projection_context.invocation != invocation:
            raise SkillContractError(
                "materialization projection must bind the exact effective invocation"
            )
        return self._materialize_bound_records(
            session_id,
            invocation.closure,
            projection_context,
            explorer_binding_env=explorer_binding_env,
            explorer_binding_env_factory=explorer_binding_env_factory,
        )

    def init_session(
        self,
        session_id: str,
        catalog: EffectiveSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> ValidatedAddDir:
        """Initialize a session from one prevalidated, path-free SESSION catalog."""
        self._validate_session_id(session_id)
        if catalog.execution_role is not SkillExecutionRole.SESSION:
            raise SkillContractError("L1 catalog materialization requires SESSION contracts")
        for member in catalog.skills:
            if member.invalidities:
                raise SkillContractError(
                    f"invalid materialization contract for {member.name!r}: "
                    f"{render_skill_invalidities(member.invalidities)}"
                )
            if member.execution_role is not SkillExecutionRole.SESSION:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"L1 catalog materialization requires SESSION members; "
                    f"{member.name!r} is {actual}"
                )
            validate_skill_capability_roles(
                member.uses_capabilities,
                member.execution_role,
            )
        if projection_context.catalog != catalog:
            raise SkillContractError(
                "materialization projection must bind the exact effective catalog"
            )
        return self._materialize_bound_records(
            session_id,
            catalog.skills,
            projection_context,
        )

    @contextmanager
    def managed_session(
        self,
        session_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> Iterator[ManagedSessionHome]:
        """Yield an already-owned generated home and clean it exactly once."""
        self._validate_session_id(session_id)
        catalog = compilation.catalog
        if catalog.execution_role not in {
            SkillExecutionRole.SESSION,
            SkillExecutionRole.ORCHESTRATOR,
        }:
            raise SkillContractError(
                "managed catalog materialization requires SESSION or ORCHESTRATOR contracts"
            )
        for member in catalog.skills:
            if member.invalidities:
                raise SkillContractError(
                    f"invalid materialization contract for {member.name!r}: "
                    f"{render_skill_invalidities(member.invalidities)}"
                )
            if member.execution_role is not catalog.execution_role:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"managed catalog materialization requires {catalog.execution_role.value} "
                    f"members; "
                    f"{member.name!r} is {actual}"
                )
            validate_skill_capability_roles(
                member.uses_capabilities,
                member.execution_role,
            )
        if projection_context.catalog != catalog:
            raise SkillContractError(
                "materialization projection must bind the exact effective catalog"
            )

        initialized = self._initialize_bound_records(
            session_id,
            catalog.skills,
            projection_context,
            compilation=compilation,
        )
        lease_fd = initialized.lease.fd if initialized.lease is not None else None
        if lease_fd is None:
            failures: list[BaseException] = [
                RuntimeError("Managed sessions require a generated-home lease")
            ]
            failures.extend(self._cleanup_owned(session_id, initialized))
            _raise_failures("Managed session setup failed", failures)
            raise AssertionError("unreachable")

        body_failure: BaseException | None = None
        try:
            yield ManagedSessionHome(
                launch_id=session_id,
                generated_home=initialized.generated_home,
                skills_dir=initialized.skills_dir,
                pass_fds=(lease_fd,),
            )
        except BaseException as exc:
            logger.error("managed_session_body_failed", exc_info=True)
            body_failure = exc

        failures = [] if body_failure is None else [body_failure]
        failures.extend(self._cleanup_owned(session_id, initialized))
        _raise_failures("Managed session body and cleanup failed", failures)

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if (
            not session_id
            or "\x00" in session_id
            or "/" in session_id
            or "\\" in session_id
            or session_id in (".", "..")
        ):
            raise ValueError(f"Invalid session_id: {session_id!r}")

    def _materialize_bound_records(
        self,
        session_id: str,
        records: tuple[SkillAuthority, ...],
        projection_context: SkillProjectionContextAuthority,
        *,
        explorer_binding_env: _ExplorerBindingEnv | None = None,
        explorer_binding_env_factory: _ExplorerBindingEnvFactory | None = None,
    ) -> ValidatedAddDir:
        return self._initialize_bound_records(
            session_id,
            records,
            projection_context,
            explorer_binding_env=explorer_binding_env,
            explorer_binding_env_factory=explorer_binding_env_factory,
        ).skills_dir

    def _initialize_bound_records(
        self,
        session_id: str,
        records: tuple[SkillAuthority, ...],
        projection_context: SkillProjectionContextAuthority,
        *,
        compilation: CompiledSessionSkillCatalogAuthority | None = None,
        explorer_binding_env: _ExplorerBindingEnv | None = None,
        explorer_binding_env_factory: _ExplorerBindingEnvFactory | None = None,
    ) -> _InitializedSession:
        self._validate_session_id(session_id)
        if explorer_binding_env is not None and explorer_binding_env_factory is not None:
            raise ValueError("provide an explorer binding map or factory, not both")
        if (
            session_id in self._session_roots
            or session_id in self._session_leases
            or session_id in self._session_skills_subdirs
            or session_id in self._session_skill_infos
        ):
            raise RuntimeError(f"Session is already owned by this manager: {session_id}")

        conventions = projection_context.conventions
        skills_subdir = (
            conventions.skills_subdir
            if conventions is not None
            else ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        )
        backend = projection_context.backend
        persistent = backend is not None and backend.capabilities.session_dir_persistent
        if backend is not None and persistent:
            configured_root = self._persistent_roots.get(backend.name)
        else:
            configured_root = self._root
        if configured_root is None:
            selected_backend = backend.name if backend is not None else None
            raise RuntimeError(
                "A persistent_root is required for persistent generated-home sessions; "
                f"selected_backend={selected_backend!r}; "
                f"configured_backend_keys={sorted(self._persistent_roots)!r}"
            )
        try:
            effective_root = configured_root.resolve()
        except OSError as exc:
            raise RuntimeError(f"Invalid generated-home root {configured_root}: {exc}") from exc
        if effective_root.exists() and not effective_root.is_dir():
            raise RuntimeError(f"Generated-home root is not a directory: {effective_root}")

        if backend is not None:
            if (
                projection_context.gating is True
                and not backend.capabilities.supports_model_invocation_gating
            ):
                raise SkillContractError(
                    f"backend {backend.name!r} does not support model invocation gating"
                )

        owned_skills_subdir = Path(SESSION_ADD_DIR_SUBDIR) / skills_subdir
        generated_home = effective_root / session_id
        lease: _SessionLease | None = None
        try:
            lease_path = effective_root / _SESSION_LEASES_SUBDIR / f"{session_id}.lock"
            lease = _SessionLease.acquire(lease_path, blocking=True)
            if lease is None:
                raise RuntimeError(f"Failed to acquire generated-home lease: {lease_path}")
            if persistent:
                _remove_and_verify(generated_home)

            skills_dir = self._materialize_session(
                generated_home,
                records,
                projection_context,
                skills_subdir=skills_subdir,
                compilation=compilation,
                explorer_binding_env=explorer_binding_env,
                explorer_binding_env_factory=explorer_binding_env_factory,
            )
            initialized = _InitializedSession(
                generated_home=generated_home,
                skills_dir=skills_dir,
                skills_subdir=owned_skills_subdir,
                lease=lease,
            )
            self._session_roots[session_id] = effective_root
            self._session_skills_subdirs[session_id] = owned_skills_subdir
            self._session_skill_infos[session_id] = {member.name: member for member in records}
            self._session_leases[session_id] = lease
            return initialized
        except BaseException as exc:
            logger.error("session_initialization_failed", exc_info=True)
            failures: list[BaseException] = [exc]
            self._session_roots.pop(session_id, None)
            self._session_skills_subdirs.pop(session_id, None)
            self._session_skill_infos.pop(session_id, None)
            self._session_leases.pop(session_id, None)
            if lease is not None and os.path.lexists(generated_home):
                try:
                    _remove_and_verify(generated_home)
                except BaseException as cleanup_exc:
                    logger.error("session_initialization_rollback_failed", exc_info=True)
                    failures.append(cleanup_exc)
            if lease is not None:
                try:
                    lease.release()
                except BaseException as release_exc:
                    logger.error("session_initialization_lease_release_failed", exc_info=True)
                    failures.append(release_exc)
            _raise_failures("Session initialization and rollback failed", failures)
            raise AssertionError("unreachable")

    def _materialize_session(
        self,
        generated_home: Path,
        records: tuple[SkillAuthority, ...],
        projection_context: SkillProjectionContextAuthority,
        *,
        skills_subdir: Path,
        compilation: CompiledSessionSkillCatalogAuthority | None = None,
        explorer_binding_env: _ExplorerBindingEnv | None = None,
        explorer_binding_env_factory: _ExplorerBindingEnvFactory | None = None,
    ) -> ValidatedAddDir:
        backend = projection_context.backend
        add_dir = generated_home / SESSION_ADD_DIR_SUBDIR
        skills_base = add_dir / skills_subdir
        skills_base.mkdir(parents=True, exist_ok=True)

        effective_catalog = projection_context.catalog
        if compilation is not None:
            effective_catalog = compilation.catalog
            records = tuple(effective_catalog.skills)
        elif backend is not None and effective_catalog is not None:
            compilation = compile_session_skill_catalog(effective_catalog, backend)
            effective_catalog = compilation.catalog
            records = tuple(effective_catalog.skills)
        elif backend is not None and projection_context.invocation is not None:
            for record in records:
                plan = record.semantic_plan
                if plan is None:
                    continue
                adaptation = backend.adapt_skill_semantics(plan)
                adaptation.validate_for(plan, backend=backend.name)

        write_skill_unavailability_metadata(
            add_dir,
            compilation=compilation,
            backend=backend.name if backend is not None else None,
        )

        execution_role = (
            effective_catalog.execution_role
            if effective_catalog is not None
            else SkillExecutionRole.SESSION
        )

        if backend is not None and backend.capabilities.mcp_config_capable:
            readiness = backend.ensure_pre_launch(session_dir=generated_home)
            if readiness.errors:
                raise RuntimeError(f"Pre-launch check failed: {'; '.join(readiness.errors)}")
        if explorer_binding_env_factory is not None:
            explorer_binding_env = explorer_binding_env_factory(generated_home)
        if backend is not None:
            setup_kwargs: _SessionSetupKwargs = {
                "parent_sandbox_mode": projection_context.parent_sandbox_mode,
                "execution_role": execution_role,
            }
            if explorer_binding_env is not None:
                setup_kwargs["explorer_binding_env"] = explorer_binding_env
            backend.setup_session_dir(generated_home, **setup_kwargs)

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
            active_exploration_applicabilities=(
                projection_context.active_exploration_applicabilities
            ),
            parent_sandbox_mode=projection_context.parent_sandbox_mode,
            explorer_provisioning_eligible=(
                explorer_binding_env is not None
                or projection_context.explorer_provisioning_eligible
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
            self._create_inert_rollout_paths(generated_home, backend)
        if backend is not None:
            layout_errors = list(
                backend.validate_session_layout(
                    generated_home,
                    project_dir=projection_context.project_root or projection_context.cwd,
                )
            )
            if layout_errors:
                raise RuntimeError("Session layout validation failed: " + "; ".join(layout_errors))
        return ValidatedAddDir(path=str(add_dir))

    @staticmethod
    def _create_inert_rollout_paths(
        generated_home: Path,
        backend: CodingAgentBackend,
    ) -> None:
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

    def _cleanup_owned(
        self,
        session_id: str,
        initialized: _InitializedSession,
    ) -> list[BaseException]:
        """Delete while leased, clear ownership maps, then release."""
        failures: list[BaseException] = []
        try:
            _remove_and_verify(initialized.generated_home)
        except BaseException as exc:
            logger.error("owned_session_cleanup_failed", exc_info=True)
            failures.append(exc)

        self._session_roots.pop(session_id, None)
        self._session_skills_subdirs.pop(session_id, None)
        self._session_skill_infos.pop(session_id, None)
        self._session_leases.pop(session_id, None)

        if initialized.lease is not None:
            try:
                initialized.lease.release()
            except BaseException as exc:
                logger.error("owned_session_lease_release_failed", exc_info=True)
                failures.append(exc)
        return failures

    def _candidate_roots(self) -> tuple[Path, ...]:
        return (self._root, *self._persistent_roots.values())

    def cleanup_session(self, session_id: str) -> bool:
        """Remove the session skill directory for a completed session.

        Returns True if the directory was found and removed, False otherwise.
        """
        self._validate_session_id(session_id)
        effective_root = self._session_roots.get(session_id)
        if effective_root is not None:
            generated_home = effective_root / session_id
            initialized = _InitializedSession(
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(path=str(generated_home)),
                skills_subdir=self._session_skills_subdirs.get(
                    session_id,
                    ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR,
                ),
                lease=self._session_leases.get(session_id),
            )
            existed = os.path.lexists(generated_home)
            failures = self._cleanup_owned(session_id, initialized)
            _raise_failures("Owned session cleanup failed", failures)
            return existed

        for root in self._candidate_roots():
            resolved_root = root.resolve()
            candidate = resolved_root / session_id
            if not os.path.lexists(candidate):
                continue
            lease = _SessionLease.acquire(
                resolved_root / _SESSION_LEASES_SUBDIR / f"{session_id}.lock",
                blocking=False,
            )
            if lease is None:
                logger.warning(
                    "cleanup_session_contended",
                    session_id=session_id,
                    root=str(resolved_root),
                )
                return False
            cleanup_failures: list[BaseException] = []
            removed = False
            try:
                removed = _remove_and_verify(candidate)
            except BaseException as exc:
                logger.error("unowned_session_cleanup_failed", exc_info=True)
                cleanup_failures.append(exc)
            try:
                lease.release()
            except BaseException as exc:
                logger.error("unowned_session_lease_release_failed", exc_info=True)
                cleanup_failures.append(exc)
            _raise_failures("Unowned session cleanup failed", cleanup_failures)
            return removed
        return False

    def validate_session_exists(self, session_id: str) -> bool:
        """Return True if session directory exists and is non-empty."""
        for root in self._candidate_roots():
            candidate = root / session_id
            if candidate.is_dir():
                try:
                    return any(candidate.iterdir())
                except OSError:
                    return False
        return False

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int:
        """Remove session dirs not accessed within max_age_seconds.

        Returns count of removed directories.
        """
        now = time.time()
        removed = 0
        for root in self._candidate_roots():
            if not root.exists():
                continue
            resolved_root = root.resolve()
            for entry in resolved_root.iterdir():
                if entry.name == _SESSION_LEASES_SUBDIR:
                    continue
                if not entry.is_dir():
                    continue
                last_access = entry.stat().st_atime
                if now - last_access > max_age_seconds:
                    if entry.name in self._session_leases:
                        continue
                    lease = _SessionLease.acquire(
                        resolved_root / _SESSION_LEASES_SUBDIR / f"{entry.name}.lock",
                        blocking=False,
                    )
                    if lease is None:
                        continue
                    failures: list[BaseException] = []
                    did_remove = False
                    try:
                        current_access = entry.stat().st_atime
                        if now - current_access > max_age_seconds:
                            did_remove = _remove_and_verify(entry)
                    except FileNotFoundError:
                        pass
                    except BaseException as exc:
                        logger.error("stale_session_cleanup_failed", exc_info=True)
                        failures.append(exc)
                    try:
                        lease.release()
                    except BaseException as exc:
                        logger.error("stale_session_lease_release_failed", exc_info=True)
                        failures.append(exc)
                    _raise_failures("Stale session cleanup failed", failures)
                    if not did_remove:
                        continue
                    logger.warning(
                        "cleanup_stale_removed",
                        path=str(entry),
                        age_seconds=round(now - last_access),
                    )
                    removed += 1
        return removed
