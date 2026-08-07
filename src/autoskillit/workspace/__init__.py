"""workspace/ IL-1 package: directory cleanup, skill resolution, and clone isolation.

Re-exports the full public surface of cleanup.py, skills.py, and clone.py.
All sub-modules depend only on autoskillit.core.*.
"""

from autoskillit.core import SkillResolver
from autoskillit.workspace._clone_detect import (
    RUNS_DIR,
    classify_remote_url,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
    detect_unpublished_branch,
)
from autoskillit.workspace._clone_remote import CloneSourceResolution
from autoskillit.workspace._install_state import (
    marketplace_plugin_root,
    reconcile_install_artifacts,
    verify_install_state,
)
from autoskillit.workspace._installed_artifact import (
    InstalledArtifactVerification,
    InstallStateFinding,
    InstallStateLeaseMode,
    InstallStateSpec,
    verify_installed_plugin_artifact,
)
from autoskillit.workspace._projected_artifact import (
    PROJECTION_CACHE_KEY_EXCLUSIONS,
    ProjectedPluginArtifactAuthority,
    ProjectedPluginRetirementOwner,
    ProjectionCacheKey,
    iter_public_plugin_asset_files,
    project_default_plugin_authority,
    project_direct_install_authority,
    prune_stale_projections,
    public_plugin_asset_digest,
)
from autoskillit.workspace.cleanup import (
    CleanupResult,
    DefaultWorkspaceManager,
    _delete_directory_contents,
)
from autoskillit.workspace.clone import (
    DefaultCloneManager,
    clone_repo,
    push_to_remote,
    remove_clone,
)
from autoskillit.workspace.clone_registry import (
    batch_delete as batch_delete,
)
from autoskillit.workspace.clone_registry import (
    cleanup_candidates as cleanup_candidates,
)
from autoskillit.workspace.clone_registry import (
    read_registry as read_registry,
)
from autoskillit.workspace.clone_registry import (
    register_clone as register_clone,
)
from autoskillit.workspace.session_skills import (
    CompiledSessionSkillCatalog,
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    SkillUnavailableMetadata,
    compile_session_skill_catalog,
    default_skill_resolver,
    materialize_codex_profile_skills,
    resolve_closure_write_dirs,
    resolve_ephemeral_root,
    resolve_persistent_session_root,
    resolve_persistent_session_roots,
    write_skill_unavailability_metadata,
)
from autoskillit.workspace.skill_capabilities import (
    RETIRED_SEMANTIC_CAPABILITIES,
    SkillCapabilityAuthenticityDiagnostic,
    SkillCapabilityEvidence,
    SkillCapabilityValidation,
    classify_skill_capability_evidence,
    detect_skill_capabilities,
    validate_skill_capability_authenticity,
    validate_skill_capability_declarations,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseError,
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
    read_skill_frontmatter,
    validate_skill_frontmatter,
)
from autoskillit.workspace.skill_projection import (
    AgentSkillDocument,
    SkillProjectionBinding,
    SkillProjectionContext,
    SkillProjectionPreparation,
    build_skill_projection_binding,
    finalize_skill_projection_binding,
    materialize_agent_skill_tree,
    materialize_sanitized_plugin_root,
    prepare_catalog_skill_projection,
    prepare_skill_projection,
    project_agent_skill_document,
    validate_sanitized_plugin_artifact,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    EffectiveSkillInvocation,
    ProjectLocalOverride,
    SkillCatalogEntry,
    SkillExclusion,
    SkillInfo,
    SkillInvalidity,
    bundled_skills_dir,
    bundled_skills_extended_dir,
    detect_project_local_overrides,
    invalidity_hints,
    override_names,
    render_skill_invalidities,
    validate_skill_tier_roles,
)
from autoskillit.workspace.worktree import (
    WORKTREES_DIR,
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
    write_worktree_sidecar,
)

delete_directory_contents = _delete_directory_contents

__all__ = [
    "batch_delete",
    "AgentSkillDocument",
    "CleanupResult",
    "cleanup_candidates",
    "delete_directory_contents",
    "classify_remote_url",
    "classify_skill_capability_evidence",
    "DefaultCloneManager",
    "DefaultWorkspaceManager",
    "DefaultSessionSkillManager",
    "list_git_worktrees",
    "read_registry",
    "register_clone",
    "remove_git_worktree",
    "remove_worktree_sidecar",
    "write_worktree_sidecar",
    "write_skill_unavailability_metadata",
    "RUNS_DIR",
    "DefaultSkillResolver",
    "CompiledSessionSkillCatalog",
    "default_skill_resolver",
    "EffectiveSkillCatalog",
    "SkillProjectionBinding",
    "SkillProjectionPreparation",
    "EffectiveSkillInvocation",
    "SkillResolver",
    "SkillCatalogEntry",
    "SkillExclusion",
    "SkillInfo",
    "SkillInvalidity",
    "SkillUnavailableMetadata",
    "SkillCapabilityAuthenticityDiagnostic",
    "SkillCapabilityEvidence",
    "SkillCapabilityValidation",
    "RETIRED_SEMANTIC_CAPABILITIES",
    "SkillFrontmatterParseError",
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "InstallStateFinding",
    "InstallStateLeaseMode",
    "InstalledArtifactVerification",
    "InstallStateSpec",
    "ProjectionCacheKey",
    "ProjectedPluginArtifactAuthority",
    "ProjectedPluginRetirementOwner",
    "SkillFrontmatterParseResult",
    "SkillProjectionContext",
    "iter_public_plugin_asset_files",
    "marketplace_plugin_root",
    "prune_stale_projections",
    "public_plugin_asset_digest",
    "reconcile_install_artifacts",
    "verify_install_state",
    "verify_installed_plugin_artifact",
    "SkillsDirectoryProvider",
    "materialize_agent_skill_tree",
    "materialize_codex_profile_skills",
    "materialize_sanitized_plugin_root",
    "validate_sanitized_plugin_artifact",
    "bundled_skills_dir",
    "bundled_skills_extended_dir",
    "build_skill_projection_binding",
    "finalize_skill_projection_binding",
    "detect_project_local_overrides",
    "invalidity_hints",
    "override_names",
    "render_skill_invalidities",
    "ProjectLocalOverride",
    "clone_repo",
    "compile_session_skill_catalog",
    "CloneSourceResolution",
    "resolve_closure_write_dirs",
    "detect_branch",
    "detect_skill_capabilities",
    "detect_source_dir",
    "detect_uncommitted_changes",
    "detect_unpublished_branch",
    "push_to_remote",
    "remove_clone",
    "resolve_ephemeral_root",
    "resolve_persistent_session_root",
    "resolve_persistent_session_roots",
    "WORKTREES_DIR",
    "parse_frontmatter_content",
    "prepare_catalog_skill_projection",
    "prepare_skill_projection",
    "project_agent_skill_document",
    "project_default_plugin_authority",
    "project_direct_install_authority",
    "read_skill_frontmatter",
    "validate_skill_tier_roles",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
    "validate_skill_frontmatter",
]
