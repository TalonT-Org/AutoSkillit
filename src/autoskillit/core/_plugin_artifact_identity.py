"""Backward-compat shim for _plugin_artifact_identity.

See ``core.plugins._plugin_artifact_identity`` for the real module.
"""

from autoskillit.core.plugins._plugin_artifact_identity import (
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_FIELDS,
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    classify_directory_tree_digest_error,
    directory_tree_digest,
    generation_artifact_root,
    generation_plugin_selector_path,
    generation_selector_path,
    generation_staging_root,
    generation_store_root,
    generation_version_root,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_manifest_payload,
    installed_plugin_artifact_root,
    installed_plugin_cache_dir,
    read_installed_plugin_artifact_identity,
    resolve_current_generation,
    resolve_current_generation_for_plugin,
)

__all__ = [
    "INSTALLED_PLUGIN_ARTIFACT_MANIFEST_FIELDS",
    "INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "classify_directory_tree_digest_error",
    "directory_tree_digest",
    "generation_artifact_root",
    "generation_plugin_selector_path",
    "generation_selector_path",
    "generation_staging_root",
    "generation_store_root",
    "generation_version_root",
    "installed_plugin_artifact_lease_path",
    "installed_plugin_artifact_manifest_path",
    "installed_plugin_artifact_manifest_payload",
    "installed_plugin_artifact_root",
    "installed_plugin_cache_dir",
    "read_installed_plugin_artifact_identity",
    "resolve_current_generation",
    "resolve_current_generation_for_plugin",
]
