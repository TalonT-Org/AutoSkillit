"""Projection of internal skill contracts into agent-visible documents.

Two module-wide policies, stated once so call sites cannot drift:

**The projection source is always ``pkg_root()``.** It is the code currently
executing, so it cannot be stale. Nothing here ever reads a plugin root out of
third-party-owned mutable state (``installed_plugins.json``, the Claude Code
plugin cache): such a path is a *derived copy* that its owner is free to
version, relocate, or garbage-collect, and treating it as a source silently
produces mixed-version sessions — old recipes/agents/hooks against new code.

**A projection destination's prior content is always replaced; only its
location is contract-relevant.** Containment checks therefore use
``destination_location()``, never ``Path.resolve()`` — resolving follows a
final-component symlink and turns "where may I write?" into "what does this
currently point at?", which is a different and wrong question.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import (
    CodingAgentBackend,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSourceIdentity,
    SkillVisibilitySpec,
    temp_dir_display_str,
)
from autoskillit.workspace._projected_artifact import (
    AgentSkillDocument,
    ProjectedPluginArtifactAuthority,
    ProjectedPluginRetirementOwner,
    SkillProjectionContext,
    materialize_agent_skill_tree,
    materialize_sanitized_plugin_root,
    project_agent_skill_document,
    project_default_plugin_authority,
    project_direct_install_authority,
    validate_sanitized_plugin_artifact,
)
from autoskillit.workspace._projected_artifact.materialization import _default_base_branch

__all__ = [
    "AgentSkillDocument",
    "EffectiveSkillDispatchContract",
    "EffectiveSkillDispatchPreparation",
    "ProjectedPluginArtifactAuthority",
    "ProjectedPluginRetirementOwner",
    "SkillProjectionContext",
    "build_effective_skill_dispatch_contract",
    "finalize_effective_skill_dispatch",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "prepare_catalog_skill_dispatch",
    "prepare_effective_skill_dispatch",
    "project_agent_skill_document",
    "project_default_plugin_authority",
    "project_direct_install_authority",
    "validate_sanitized_plugin_artifact",
]


@dataclass(frozen=True, slots=True)
class EffectiveSkillDispatchContract:
    """Immutable execution-bound contract carried through headless dispatch."""

    resolved_command: str
    projection_context: SkillProjectionContext
    invocation: EffectiveSkillInvocationAuthority | None
    catalog: EffectiveSkillCatalogAuthority | None
    root_name: str | None
    member_names: tuple[str, ...]
    execution_role: SkillExecutionRole
    capability_union: frozenset[str]
    source_identities: Mapping[str, SkillSourceIdentity]
    canonical_digests: Mapping[str, str]
    projected_digests: Mapping[str, str]
    projected_artifacts: Mapping[str, str]
    projection_version: int
    project_root: str | None
    cwd: str
    backend: str | None
    artifact_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.resolved_command:
            raise SkillContractError("effective dispatch requires a resolved command")
        if (self.invocation is None) == (self.catalog is None):
            raise SkillContractError(
                "effective dispatch must bind exactly one invocation or catalog"
            )
        expected_names = tuple(skill.name for skill in self.projection_context.skills)
        if self.member_names != expected_names:
            raise SkillContractError("effective dispatch member order does not match projection")
        expected_set = set(expected_names)
        for field_name, mapping in (
            ("source identities", self.source_identities),
            ("canonical digests", self.canonical_digests),
            ("projected digests", self.projected_digests),
            ("projected artifacts", self.projected_artifacts),
        ):
            if set(mapping) != expected_set:
                raise SkillContractError(
                    f"effective dispatch {field_name} do not match its members"
                )
            object.__setattr__(self, field_name.replace(" ", "_"), MappingProxyType(dict(mapping)))


@dataclass(frozen=True, slots=True)
class EffectiveSkillDispatchPreparation:
    """Backend-neutral semantic inputs awaiting one exact launch binding."""

    resolved_command: str
    cwd: Path
    project_root: Path | None
    default_base_branch: str
    catalog: EffectiveSkillCatalogAuthority | None = None
    invocation: EffectiveSkillInvocationAuthority | None = None

    def __post_init__(self) -> None:
        if not self.resolved_command:
            raise SkillContractError("dispatch preparation requires a resolved command")
        if (self.catalog is None) == (self.invocation is None):
            raise SkillContractError(
                "dispatch preparation must bind exactly one catalog or invocation"
            )
        object.__setattr__(self, "cwd", Path(self.cwd))
        if self.project_root is not None:
            object.__setattr__(self, "project_root", Path(self.project_root))

    def finalize(
        self,
        *,
        backend: CodingAgentBackend,
        binding: PluginLaunchBinding,
    ) -> EffectiveSkillDispatchContract:
        return _finalize_effective_skill_dispatch(
            self,
            backend=backend,
            binding=binding,
        )


def build_effective_skill_dispatch_contract(
    resolved_command: str,
    projection_context: SkillProjectionContext,
    *,
    artifact_paths: Iterable[str] = (),
) -> EffectiveSkillDispatchContract:
    """Freeze role, identity, digests, and projected bytes for executor handoff."""
    documents = {
        skill.name: project_agent_skill_document(skill, projection_context)
        for skill in projection_context.skills
    }
    invocation = projection_context.invocation
    catalog = projection_context.catalog
    if invocation is not None:
        execution_role = invocation.execution_role
    else:
        assert catalog is not None
        execution_role = catalog.execution_role
    capability_union = frozenset().union(
        *(skill.uses_capabilities for skill in projection_context.skills)
    )
    backend = projection_context.backend
    return EffectiveSkillDispatchContract(
        resolved_command=resolved_command,
        projection_context=projection_context,
        invocation=invocation,
        catalog=catalog,
        root_name=invocation.root.name if invocation is not None else None,
        member_names=tuple(skill.name for skill in projection_context.skills),
        execution_role=execution_role,
        capability_union=capability_union,
        source_identities={name: document.source_identity for name, document in documents.items()},
        canonical_digests={
            name: document.canonical_digest for name, document in documents.items()
        },
        projected_digests={
            name: document.projected_digest for name, document in documents.items()
        },
        projected_artifacts={name: document.content for name, document in documents.items()},
        projection_version=projection_context.projection_version,
        project_root=(
            str(projection_context.project_root)
            if projection_context.project_root is not None
            else None
        ),
        cwd=str(projection_context.cwd),
        backend=backend.name if backend is not None else None,
        artifact_paths=tuple(artifact_paths),
    )


def _finalize_effective_skill_dispatch(
    preparation: EffectiveSkillDispatchPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> EffectiveSkillDispatchContract:
    """Bind backend conventions only after the launch artifact is reader-owned."""
    if binding.closed:
        raise PluginArtifactValidationError(
            "cannot finalize dispatch with a closed plugin launch binding"
        )
    if binding.plugin_dir is not None and binding.plugin_dir != binding.identity.managed_path:
        raise PluginArtifactValidationError(
            "plugin launch path does not match its leased artifact identity"
        )
    destination = binding.plugin_dir or binding.identity.managed_path
    context = SkillProjectionContext(
        cwd=preparation.cwd,
        project_root=preparation.project_root,
        catalog=preparation.catalog,
        invocation=preparation.invocation,
        backend=backend,
        conventions=backend.conventions,
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": preparation.default_base_branch,
        },
    )
    return build_effective_skill_dispatch_contract(
        preparation.resolved_command,
        context,
        artifact_paths=(),
    )


def finalize_effective_skill_dispatch(
    preparation: EffectiveSkillDispatchPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> EffectiveSkillDispatchContract:
    """Workspace convenience wrapper around preparation-owned finalization."""
    return preparation.finalize(backend=backend, binding=binding)


def prepare_catalog_skill_dispatch(
    *,
    resolved_command: str,
    cwd: Path,
    catalog: EffectiveSkillCatalogAuthority,
    default_base_branch: str,
    project_root: Path | None = None,
) -> tuple[ProjectedPluginArtifactAuthority, EffectiveSkillDispatchPreparation]:
    """Prepare semantic dispatch state without acquiring or publishing an artifact."""
    default_base_branch = _default_base_branch(default_base_branch)
    authority = project_default_plugin_authority(
        cwd=cwd,
        base_branch=default_base_branch,
        catalog=catalog,
    )
    preparation = EffectiveSkillDispatchPreparation(
        resolved_command=resolved_command,
        cwd=cwd,
        project_root=project_root,
        default_base_branch=default_base_branch,
        catalog=catalog,
    )
    return authority, preparation


def prepare_effective_skill_dispatch(
    *,
    resolved_command: str,
    project_root: Path,
    cwd: Path,
    resolver: SkillResolver,
    visibility: SkillVisibilitySpec | None,
    default_base_branch: str | None,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[ProjectedPluginArtifactAuthority, EffectiveSkillDispatchPreparation]:
    """Resolve visible orchestrator skills into a backend-neutral preparation."""
    catalog = resolver.list_effective(
        project_root,
        SkillExecutionRole.ORCHESTRATOR,
        visibility=visibility,
        recipe_packs=recipe_packs,
        recipe_features=recipe_features,
    )
    return prepare_catalog_skill_dispatch(
        resolved_command=resolved_command,
        cwd=cwd,
        catalog=catalog,
        default_base_branch=_default_base_branch(default_base_branch),
        project_root=project_root,
    )
