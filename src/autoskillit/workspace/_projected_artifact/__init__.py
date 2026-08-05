"""Projected plugin artifact lifecycle facade."""

from .._projection_cache import (
    PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    PROJECTION_CACHE_KEY_EXCLUSIONS,
    ProjectedPluginRetirementOwner,
    ProjectionCacheKey,
    is_projected_asset,
    iter_public_plugin_asset_files,
    projected_artifact_lease_path,
    projected_artifact_manifest_path,
    projected_plugin_artifact_digest,
    prune_stale_projections,
    public_plugin_asset_digest,
    read_projected_plugin_identity,
)
from ._hook_repair import RepairOutcome, repair_broken_plugin_cache_hooks
from ._manifest_publication import write_installed_plugin_artifact_manifest_locked
from .authority import (
    ProjectedPluginArtifactAuthority,
    project_default_plugin_authority,
    project_direct_install_authority,
)
from .materialization import (
    AgentSkillDocument,
    SkillProjectionContext,
    materialize_agent_skill_tree,
    materialize_sanitized_plugin_root,
    project_agent_skill_document,
    validate_sanitized_plugin_artifact,
)

__all__ = [
    "AgentSkillDocument",
    "PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "ProjectedPluginArtifactAuthority",
    "ProjectedPluginRetirementOwner",
    "ProjectionCacheKey",
    "RepairOutcome",
    "SkillProjectionContext",
    "is_projected_asset",
    "iter_public_plugin_asset_files",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "project_agent_skill_document",
    "project_default_plugin_authority",
    "project_direct_install_authority",
    "projected_artifact_lease_path",
    "projected_artifact_manifest_path",
    "projected_plugin_artifact_digest",
    "prune_stale_projections",
    "public_plugin_asset_digest",
    "read_projected_plugin_identity",
    "repair_broken_plugin_cache_hooks",
    "validate_sanitized_plugin_artifact",
    "write_installed_plugin_artifact_manifest_locked",
]
