"""Session-skill manager — orchestrator of per-session ephemeral skill dirs.

Single owner of ``DefaultSessionSkillManager`` and ``_InitializedSession``.
Manager-owned bound-record initialization, public protocol surface
(materialize_invocation, init_session, managed_session, cleanup_session,
validate_session_exists, cleanup_stale) and the four ownership maps
(``_session_roots``, ``_session_skills_subdirs``, ``_session_skill_infos``,
``_session_leases``) live here.

Lifecycle primitives (lease/removal) and materialization transaction are
imported from sibling shards; the manager composes them without introducing
another state object or protocol.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    SESSION_ADD_DIR_SUBDIR,
    SESSION_STALE_SECONDS,
    ClaudeDirectoryConventions,
    CompiledSessionSkillCatalogAuthority,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    ManagedSessionHome,
    SkillAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillProjectionContextAuthority,
    SkillUnavailabilityPayload,
    ValidatedAddDir,
    get_logger,
    validate_skill_capability_roles,
)
from autoskillit.workspace.session_skill_catalog import (
    _canonical_skill_unavailability_payload,
)
from autoskillit.workspace.session_skill_lifecycle import (
    _SESSION_LEASES_SUBDIR,
    _raise_failures,
    _remove_and_verify,
    _SessionLease,
)
from autoskillit.workspace.session_skill_materialization import (
    _ExplorerBindingEnv,
    _ExplorerBindingEnvFactory,
    _materialize_session,
)
from autoskillit.workspace.session_skill_provider import SkillsDirectoryProvider
from autoskillit.workspace.skills import render_skill_invalidities

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _InitializedSession:
    generated_home: Path
    skills_dir: ValidatedAddDir
    skills_subdir: Path
    lease: _SessionLease | None
    unavailability_payload: SkillUnavailabilityPayload


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

    @property
    def ephemeral_root(self) -> Path:
        """Return the root used for ephemeral session directories."""
        return self._root

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
                unavailability_payload=initialized.unavailability_payload,
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

            skills_dir, finalized_records, unavailability_payload = _materialize_session(
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
                unavailability_payload=unavailability_payload,
            )
            self._session_roots[session_id] = effective_root
            self._session_skills_subdirs[session_id] = owned_skills_subdir
            self._session_skill_infos[session_id] = {
                member.name: member for member in finalized_records
            }
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
                unavailability_payload=_canonical_skill_unavailability_payload(None, ()),
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

    def cleanup_stale(self, max_age_seconds: int = SESSION_STALE_SECONDS) -> int:
        """Remove session dirs not modified within max_age_seconds.

        Gates on st_mtime, not st_atime -- /dev/shm is commonly mounted noatime, which
        leaves an atime gate frozen at creation and non-functional. max_age_seconds
        defaults to SESSION_STALE_SECONDS, the same constant and stat field
        scripts/pytest_tmp_lifecycle.py's sweep-sessions subcommand uses for the same root
        via the Taskfile, so the two no longer disagree on when this root's entries are
        stale (see core/runtime/_reclamation.py).

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
                last_modified = entry.stat().st_mtime
                if now - last_modified > max_age_seconds:
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
                        current_mtime = entry.stat().st_mtime
                        if now - current_mtime > max_age_seconds:
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
                        age_seconds=round(now - last_modified),
                    )
                    removed += 1
        return removed


__all__ = [
    "DefaultSessionSkillManager",
    "_InitializedSession",
]
