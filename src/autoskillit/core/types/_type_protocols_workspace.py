"""Workspace and skill management protocol definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_backend import BackendConventions
from ._type_enums import SkillExecutionRole, SkillSource
from ._type_protocols_backend import CodingAgentBackend
from ._type_results import CleanupResult, CloneResult, ManagedSessionHome, ValidatedAddDir
from ._type_skill_contract import SkillSourceIdentity, SkillSourceRef, SkillVisibilitySpec

__all__ = [
    "WorkspaceManager",
    "CloneManager",
    "EffectiveSkillCatalogAuthority",
    "EffectiveSkillInvocationAuthority",
    "ResolvedSkillAuthority",
    "SessionSkillManager",
    "SkillAuthority",
    "SkillFrontmatterAuthority",
    "SkillLister",
    "SkillProjectionContextAuthority",
    "SkillResolver",
]


@runtime_checkable
class SkillFrontmatterAuthority(Protocol):
    """Parsed frontmatter fields required by projection without an IL-1 import."""

    @property
    def data(self) -> Mapping[str, Any] | None: ...

    @property
    def is_valid(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    @property
    def body(self) -> str: ...


@runtime_checkable
class SkillAuthority(Protocol):
    """Structural machine authority shared by resolved and catalog skill records."""

    @property
    def name(self) -> str: ...

    @property
    def source(self) -> SkillSource: ...

    @property
    def source_identity(self) -> SkillSourceIdentity: ...

    @property
    def categories(self) -> frozenset[str]: ...

    @property
    def uses_capabilities(self) -> frozenset[str]: ...

    @property
    def execution_role(self) -> SkillExecutionRole | None: ...

    @property
    def activate_deps(self) -> tuple[str, ...]: ...

    @property
    def canonical_content(self) -> str: ...

    @property
    def canonical_digest(self) -> str: ...

    @property
    def frontmatter(self) -> SkillFrontmatterAuthority | None: ...

    @property
    def invalid_reason(self) -> str | None: ...

    @property
    def backend_requirements(self) -> frozenset[str]: ...


@runtime_checkable
class ResolvedSkillAuthority(SkillAuthority, Protocol):
    """Structural authority for one source-resolved skill."""

    @property
    def path(self) -> Path: ...

    @property
    def source_ref(self) -> SkillSourceRef | None: ...


@runtime_checkable
class EffectiveSkillCatalogAuthority(Protocol):
    """Role-filtered immutable skill catalog crossing the IL-0 boundary."""

    @property
    def skills(self) -> tuple[SkillAuthority, ...]: ...

    @property
    def execution_role(self) -> SkillExecutionRole: ...

    @property
    def namespace_sources(self) -> Mapping[str, SkillSource]: ...


@runtime_checkable
class EffectiveSkillInvocationAuthority(Protocol):
    """Resolved root and complete executable closure crossing the IL-0 boundary."""

    @property
    def root(self) -> ResolvedSkillAuthority: ...

    @property
    def closure(self) -> tuple[ResolvedSkillAuthority, ...]: ...

    @property
    def capability_union(self) -> frozenset[str]: ...

    @property
    def project_root(self) -> Path | None: ...

    @property
    def execution_role(self) -> SkillExecutionRole: ...

    @property
    def backend_requirements(self) -> frozenset[str]: ...


@runtime_checkable
class SkillProjectionContextAuthority(Protocol):
    """Projection inputs bound to an invocation or catalog."""

    @property
    def cwd(self) -> Path: ...

    @property
    def project_root(self) -> Path | None: ...

    @property
    def catalog(self) -> EffectiveSkillCatalogAuthority | None: ...

    @property
    def invocation(self) -> EffectiveSkillInvocationAuthority | None: ...

    @property
    def backend(self) -> CodingAgentBackend | None: ...

    @property
    def conventions(self) -> BackendConventions | None: ...

    @property
    def substitutions(self) -> Mapping[str, str] | None: ...

    @property
    def gating(self) -> bool | None: ...

    @property
    def namespace(self) -> str | None: ...

    @property
    def projection_version(self) -> int: ...

    @property
    def skills(self) -> tuple[SkillAuthority, ...]: ...


@runtime_checkable
class WorkspaceManager(Protocol):
    """Protocol for directory teardown operations."""

    def delete_contents(
        self,
        directory: Path,
        preserve: set[str] | None = None,
    ) -> CleanupResult: ...


@runtime_checkable
class CloneManager(Protocol):
    """Protocol for clone-based pipeline run isolation."""

    def clone_repo(
        self,
        source_dir: str,
        run_name: str,
        branch: str = "",
        strategy: str = "",
        remote_url: str = "",
    ) -> CloneResult: ...

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]: ...

    def push_to_remote(
        self,
        clone_path: str,
        source_dir: str = "",
        branch: str = "",
        *,
        remote_url: str = "",
        protected_branches: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, str | bool]: ...


@runtime_checkable
class SessionSkillManager(Protocol):
    """Protocol for managing per-session ephemeral skill directories."""

    def managed_session(
        self,
        session_id: str,
        catalog: EffectiveSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> AbstractContextManager[ManagedSessionHome]: ...

    def materialize_invocation(
        self,
        session_id: str,
        invocation: EffectiveSkillInvocationAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> ValidatedAddDir: ...

    def init_session(
        self,
        session_id: str,
        catalog: EffectiveSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> ValidatedAddDir: ...

    def cleanup_session(self, session_id: str) -> bool: ...

    def validate_session_exists(self, session_id: str) -> bool: ...

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int: ...


@runtime_checkable
class SkillResolver(Protocol):
    """Protocol for resolving skill names to their source tier."""

    def resolve(self, name: str) -> ResolvedSkillAuthority | None: ...

    def resolve_effective(
        self,
        name: str,
        project_root: Path | None,
    ) -> ResolvedSkillAuthority | None: ...

    def list_effective(
        self,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        visibility: SkillVisibilitySpec | None = None,
        cook_session: bool = False,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
    ) -> EffectiveSkillCatalogAuthority: ...

    def resolve_invocation(
        self,
        name: str,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        visibility: SkillVisibilitySpec | None = None,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
    ) -> EffectiveSkillInvocationAuthority: ...


@runtime_checkable
class SkillLister(Protocol):
    """IL-0 contract for listing all available skills.

    Allows IL-2 recipe rules to type their skill-listing dependency
    against an IL-0 protocol instead of binding to the IL-1 workspace
    concrete class. The default implementation lives at
    autoskillit.workspace.skills.DefaultSkillResolver and satisfies this
    protocol structurally.
    """

    def list_all(self) -> Sequence[ResolvedSkillAuthority]: ...
