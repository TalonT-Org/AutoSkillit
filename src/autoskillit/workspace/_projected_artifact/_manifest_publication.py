"""Shared manifest-publication core for the installed-plugin artifact cache.

Extracted so ``cli._plugin_artifact.publish_installed_plugin_artifact`` (fresh
identity after a successful install/upgrade) and
``workspace._projected_artifact._hook_repair.repair_broken_plugin_cache_hooks``
(manifest refresh after an in-process ``hooks.json`` repair) write the SAME
manifest through the SAME code path — one implementation, two callers.
Rewriting a cache ``hooks.json`` without refreshing its manifest through this
function would desync the digest recorded on disk from the bytes actually
there, turning the tamper detector (the digest comparison in
``verify_installed_plugin_artifact``) into a false alarm.

``cli/_plugin_artifact.py`` importing this module is a legal downward
``cli → workspace`` edge (REQ-ARCH-003b only forbids the reverse). Both
callers already hold the incarnation's exclusive publication lease before
calling in; this function performs no locking of its own.
"""

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
    """Build a fresh identity and persist its manifest for one incarnation.

    Caller must already own the incarnation's exclusive publication lease.
    ``action`` is the structured-logging verb recorded via
    ``log_plugin_artifact_lifecycle`` (``"publish"`` for a first publish,
    ``"repair"`` for a post-repair manifest refresh).
    """
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
