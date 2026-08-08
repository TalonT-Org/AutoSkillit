"""Workspace and skill management protocol definitions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_backend import BackendConventions
from ._type_enums import SkillExecutionRole, SkillInvalidityKind, SkillSource
from ._type_exploration import RepositoryProfileId
from ._type_plugin_source import (
    PluginArtifactIdentity,
    PluginLaunchBinding,
    PluginLoadMode,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
)
from ._type_protocols_backend import CodingAgentBackend
from ._type_results import CleanupResult, CloneResult, ManagedSessionHome, ValidatedAddDir
from ._type_skill_contract import (
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    SkillSourceIdentity,
    SkillSourceRef,
    SkillVisibilitySpec,
)
from ._type_skill_semantics import SkillSemanticPlan

__all__ = [
    "WorkspaceManager",
    "PluginArtifactAuthority",
    "PluginArtifactRetirementOwner",
    "PluginRetirementCoordinator",
    "CloneManager",
    "EffectiveSkillCatalogAuthority",
    "EffectiveSkillInvocationAuthority",
    "ResolvedSkillAuthority",
    "SessionSkillManager",
    "SkillAuthority",
    "SkillExclusionAuthority",
    "SkillFrontmatterAuthority",
    "SkillInvalidityAuthority",
    "SkillLister",
    "SkillProjectionContextAuthority",
    "SkillResolver",
]


@runtime_checkable
class PluginArtifactAuthority(Protocol):
    """Lazy authority that binds an exact plugin incarnation to one launch."""

    def acquire_launch_binding(
        self,
        *,
        backend: CodingAgentBackend,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding: ...


@runtime_checkable
class PluginArtifactRetirementOwner(Protocol):
    """Artifact-specific exact-identity retirement authority."""

    @property
    def managed_root(self) -> Path: ...

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
    ) -> RetiringAppendResult: ...

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...]: ...

    def try_reclaim(
        self,
        record: RetiringArtifactRecord,
        now: datetime,
    ) -> RetirementOutcome: ...


@runtime_checkable
class PluginRetirementCoordinator(Protocol):
    """Cross-kind dispatcher injected into server startup."""

    def sweep_due(self, now: datetime) -> tuple[RetirementOutcome, ...]: ...


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
class SkillInvalidityAuthority(Protocol):
    """One typed reason a skill's contract failed, crossing the IL-0 boundary."""

    @property
    def kind(self) -> SkillInvalidityKind: ...

    @property
    def detail(self) -> str: ...


@runtime_checkable
class SkillExclusionAuthority(Protocol):
    """One project-local skill candidate excluded from the effective catalog."""

    @property
    def name(self) -> str: ...

    @property
    def path(self) -> Path: ...

    @property
    def search_dir(self) -> str: ...

    @property
    def invalidities(self) -> tuple[SkillInvalidityAuthority, ...]: ...

    @property
    def fallback(self) -> SkillSource | None: ...

    @property
    def hints(self) -> tuple[str, ...]: ...


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
    def semantic_plan(self) -> SkillSemanticPlan | None: ...

    @property
    def execution_role(self) -> SkillExecutionRole | None: ...

    @property
    def activate_deps(self) -> tuple[str, ...]: ...

    @property
    def exploration_vectors(self) -> tuple[ExplorationVectorDef, ...]: ...

    @property
    def exploration_sidecar_digest(self) -> str: ...

    @property
    def canonical_content(self) -> str: ...

    @property
    def canonical_digest(self) -> str: ...

    @property
    def frontmatter(self) -> SkillFrontmatterAuthority | None: ...

    @property
    def invalidities(self) -> tuple[SkillInvalidityAuthority, ...]: ...


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

    @property
    def exclusions(self) -> Sequence[SkillExclusionAuthority]: ...


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
    def semantic_plans(self) -> tuple[SkillSemanticPlan, ...]: ...


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
    def exploration_launch_context_ref(self) -> str | None: ...

    @property
    def resolved_exploration_profile(self) -> RepositoryProfileId | None: ...

    @property
    def active_exploration_applicabilities(
        self,
    ) -> frozenset[ExplorationVectorApplicabilityId]: ...

    @property
    def parent_sandbox_mode(self) -> str: ...

    @property
    def explorer_provisioning_eligible(self) -> bool | None: ...

    @property
    def projection_version(self) -> int: ...

    @property
    def skills(self) -> tuple[SkillAuthority, ...]: ...

    @property
    def exploration_vectors(self) -> Mapping[str, tuple[ExplorationVectorDef, ...]]: ...


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
        *,
        explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
        explorer_binding_env_factory: (
            Callable[[Path], Mapping[str, Mapping[str, str]] | None] | None
        ) = None,
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
