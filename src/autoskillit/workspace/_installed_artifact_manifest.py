"""Publish installed-plugin manifests under the installed-artifact authority."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactValidationError,
    directory_tree_digest,
    get_logger,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_manifest_payload,
    log_plugin_artifact_lifecycle,
    new_plugin_artifact_incarnation_id,
    write_versioned_json,
)

__all__ = ["write_installed_plugin_artifact_manifest_locked"]

logger = get_logger(__name__)


def write_installed_plugin_artifact_manifest_locked(
    managed_path: Path,
    *,
    semantic_key: str,
    action: str,
) -> PluginArtifactIdentity:
    """Persist a fresh identity while the caller owns the publication lease."""
    manifest_path = installed_plugin_artifact_manifest_path(managed_path)
    identity = PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=new_plugin_artifact_incarnation_id(),
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=_complete_tree_digest(managed_path),
        managed_path=managed_path,
        manifest_path=manifest_path,
    )
    write_versioned_json(
        manifest_path,
        installed_plugin_artifact_manifest_payload(identity),
        schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        strict_durability=True,
    )
    log_plugin_artifact_lifecycle(
        logger,
        action=action,
        outcome="succeeded",
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
        semantic_key=identity.semantic_key,
        incarnation=identity.incarnation_id,
    )
    return identity


def _complete_tree_digest(root: Path) -> str:
    """Hash every relative entry, kind, mode, and regular-file byte."""
    try:
        return directory_tree_digest(root)
    except (OSError, ValueError) as exc:
        raise PluginArtifactValidationError(
            f"installed plugin artifact cannot be digested: {root}"
        ) from exc
