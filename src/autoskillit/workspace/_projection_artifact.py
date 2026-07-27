"""One-way projection identity, manifest, and digest contracts."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    PluginArtifactIdentity,
    PluginArtifactValidationError,
    directory_tree_digest,
    is_canonical_plugin_artifact_digest,
    is_canonical_plugin_artifact_incarnation_id,
    read_versioned_json,
)

PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION = 2


def projected_artifact_manifest_path(managed_path: Path) -> Path:
    """Return the stable sidecar manifest for a projected root."""
    managed_path = Path(managed_path)
    return managed_path.parent / f".{managed_path.name}.autoskillit-projection.json"


def projected_artifact_lease_path(managed_path: Path) -> Path:
    """Return the stable lease sidecar for a projected root."""
    managed_path = Path(managed_path)
    return managed_path.parent / ".artifact-leases" / f"{managed_path.name}.lock"


def projected_plugin_artifact_digest(public_root: Path) -> str:
    """Hash the complete projection with the canonical artifact-tree contract."""
    try:
        return directory_tree_digest(public_root)
    except (OSError, ValueError) as exc:
        raise PluginArtifactValidationError(
            f"projected plugin artifact cannot be digested: {public_root}"
        ) from exc


def read_projected_plugin_identity(
    managed_path: Path,
    *,
    manifest_path: Path,
    expected_semantic_key: str,
    expected_projection_version: int | None = None,
) -> PluginArtifactIdentity:
    """Read and validate one exact projected artifact identity."""
    managed_path = Path(managed_path)
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is not a regular file: {manifest_path}"
        )
    manifest = read_versioned_json(
        manifest_path,
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
    if manifest is None:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is unreadable: {manifest_path}"
        )
    semantic_key = manifest.get("semantic_key")
    if semantic_key != expected_semantic_key:
        raise PluginArtifactValidationError(
            f"projected plugin semantic key mismatch: {manifest_path}"
        )
    incarnation_id = manifest.get("incarnation_id")
    if not is_canonical_plugin_artifact_incarnation_id(incarnation_id):
        raise PluginArtifactValidationError(
            f"projected plugin incarnation is not canonical uuid4 hex: {manifest_path}"
        )
    artifact_digest = manifest.get("artifact_digest")
    if not is_canonical_plugin_artifact_digest(artifact_digest):
        raise PluginArtifactValidationError(f"projected plugin digest is invalid: {manifest_path}")
    if (
        expected_projection_version is not None
        and manifest.get("projection_version") != expected_projection_version
    ):
        raise PluginArtifactValidationError(f"projected plugin version mismatch: {manifest_path}")
    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        manifest_schema_version=PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=artifact_digest,
        managed_path=managed_path,
        manifest_path=manifest_path,
    )
