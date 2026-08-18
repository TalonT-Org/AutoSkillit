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

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    CodingAgentBackend,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    SkillAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillProjectionBinding,
    SkillResolver,
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
    write_generated_hooks_json,
)
from autoskillit.workspace._projected_artifact.materialization import _default_base_branch

__all__ = [
    "AgentSkillDocument",
    "SkillProjectionBinding",
    "SkillProjectionPreparation",
    "ProjectedPluginArtifactAuthority",
    "ProjectedPluginRetirementOwner",
    "SkillProjectionContext",
    "build_skill_projection_binding",
    "finalize_skill_projection_binding",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "prepare_catalog_skill_projection",
    "prepare_skill_projection",
    "project_agent_skill_document",
    "project_default_plugin_authority",
    "project_direct_install_authority",
    "validate_sanitized_plugin_artifact",
    "write_generated_hooks_json",
]


@dataclass(frozen=True, slots=True)
class SkillProjectionPreparation:
    """Backend-neutral semantic inputs awaiting one exact launch binding."""

    cwd: Path
    project_root: Path | None
    default_base_branch: str
    catalog: EffectiveSkillCatalogAuthority | None = None
    invocation: EffectiveSkillInvocationAuthority | None = None

    def __post_init__(self) -> None:
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
    ) -> SkillProjectionBinding:
        return _finalize_skill_projection_binding(
            self,
            backend=backend,
            binding=binding,
        )


def build_skill_projection_binding(
    projection_context: SkillProjectionContext,
    *,
    artifact_paths: Iterable[str] = (),
) -> SkillProjectionBinding:
    """Freeze backend-adapted projection evidence without owning an executable."""
    backend = projection_context.backend
    if backend is None:
        raise SkillContractError("skill projection binding requires an effective backend")
    # The backend fails closed (raises SkillContractError) directly from
    # adapt_skill_semantics() when it cannot honestly realize a declared
    # operation (e.g. Codex + join.required=true) — project_agent_skill_document()
    # surfaces that raise. Exclude such skills from this binding instead of
    # failing the whole projection: catalogs reaching this point (e.g. the
    # ORCHESTRATOR-role catalog used by food-truck/kitchen dispatch) are
    # prepared before a backend is selected, so they have not necessarily
    # already passed through compile_session_skill_catalog()'s exclusion.
    documents: dict[str, AgentSkillDocument] = {}
    skills: list[SkillAuthority] = []
    for skill in projection_context.skills:
        try:
            documents[skill.name] = project_agent_skill_document(skill, projection_context)
        except SkillContractError:
            continue
        skills.append(skill)
    invocation = projection_context.invocation
    catalog = projection_context.catalog
    if invocation is not None:
        execution_role = invocation.execution_role
    else:
        assert catalog is not None
        execution_role = catalog.execution_role
    capability_union = frozenset().union(*(skill.uses_capabilities for skill in skills))
    return SkillProjectionBinding(
        root_name=invocation.root.name if invocation is not None else None,
        member_names=tuple(skill.name for skill in skills),
        execution_role=execution_role.value,
        capability_union=capability_union,
        source_identities={
            name: {
                "origin": document.source_identity.origin.value,
                "logical_name": document.source_identity.logical_name,
                "search_dir": document.source_identity.search_dir,
                "precedence": document.source_identity.precedence,
            }
            for name, document in documents.items()
        },
        canonical_digests={
            name: document.canonical_digest for name, document in documents.items()
        },
        projected_digests={
            name: document.projected_digest for name, document in documents.items()
        },
        semantic_digests={name: document.semantic_digest for name, document in documents.items()},
        adaptation_digests={
            name: document.adaptation_digest for name, document in documents.items()
        },
        projection_version=projection_context.projection_version,
        project_root=(
            str(projection_context.project_root)
            if projection_context.project_root is not None
            else None
        ),
        cwd=str(projection_context.cwd),
        backend=backend.name,
        artifact_paths=tuple(artifact_paths),
    )


def _finalize_skill_projection_binding(
    preparation: SkillProjectionPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> SkillProjectionBinding:
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
    return build_skill_projection_binding(
        context,
        artifact_paths=(str(destination),),
    )


def finalize_skill_projection_binding(
    preparation: SkillProjectionPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> SkillProjectionBinding:
    """Workspace convenience wrapper around preparation-owned finalization."""
    return preparation.finalize(backend=backend, binding=binding)


def prepare_catalog_skill_projection(
    *,
    cwd: Path,
    catalog: EffectiveSkillCatalogAuthority,
    default_base_branch: str,
    project_root: Path | None = None,
) -> tuple[ProjectedPluginArtifactAuthority, SkillProjectionPreparation]:
    """Prepare semantic dispatch state without acquiring or publishing an artifact."""
    default_base_branch = _default_base_branch(default_base_branch)
    authority = project_default_plugin_authority(
        cwd=cwd,
        base_branch=default_base_branch,
        catalog=catalog,
    )
    preparation = SkillProjectionPreparation(
        cwd=cwd,
        project_root=project_root,
        default_base_branch=default_base_branch,
        catalog=catalog,
    )
    return authority, preparation


def prepare_skill_projection(
    *,
    project_root: Path,
    cwd: Path,
    resolver: SkillResolver,
    visibility: SkillVisibilitySpec | None,
    default_base_branch: str | None,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[ProjectedPluginArtifactAuthority, SkillProjectionPreparation]:
    """Resolve visible orchestrator skills into a backend-neutral preparation."""
    catalog = resolver.list_effective(
        project_root,
        SkillExecutionRole.ORCHESTRATOR,
        visibility=visibility,
        recipe_packs=recipe_packs,
        recipe_features=recipe_features,
    )
    return prepare_catalog_skill_projection(
        cwd=cwd,
        catalog=catalog,
        default_base_branch=_default_base_branch(default_base_branch),
        project_root=project_root,
    )
