"""Installed-plugin artifact identity serialization and validation."""

from __future__ import annotations

import stat
from pathlib import Path

from ._plugin_ids import DIRECT_INSTALL_CACHE_SUBDIR
from .io import directory_tree_digest, read_versioned_json
from .types import (
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    is_canonical_plugin_artifact_digest,
    is_canonical_plugin_artifact_incarnation_id,
)


def installed_plugin_cache_dir(home: Path, plugin_ref: str) -> Path:
    """Return the managed cache directory containing installed plugin versions."""
    plugin_name = plugin_ref.partition("@")[0]
    return Path(home) / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / plugin_name


def installed_plugin_artifact_root(
    home: Path,
    plugin_ref: str,
    version: str,
) -> Path:
    """Return the managed root for one installed plugin version."""
    return installed_plugin_cache_dir(home, plugin_ref) / version


def installed_plugin_artifact_manifest_path(managed_root: Path) -> Path:
    """Return the stable external manifest for one installed plugin root."""
    root = Path(managed_root)
    return root.parent / f".{root.name}.autoskillit-artifact.json"


def installed_plugin_artifact_lease_path(managed_root: Path) -> Path:
    """Return the stable lease sidecar for one installed plugin root."""
    manifest_path = installed_plugin_artifact_manifest_path(managed_root)
    return manifest_path.with_suffix(manifest_path.suffix + ".lock")


INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
INSTALLED_PLUGIN_ARTIFACT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "semantic_key",
        "incarnation_id",
        "artifact_digest",
        "managed_path",
        "manifest_path",
    }
)


def installed_plugin_artifact_manifest_payload(
    identity: PluginArtifactIdentity,
) -> dict[str, object]:
    """Return the canonical installed-plugin manifest payload."""
    if identity.manifest_schema_version != INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION:
        raise PluginArtifactValidationError(
            "installed plugin identity uses an unsupported manifest schema"
        )
    return {
        "artifact_kind": PluginArtifactKind.INSTALLED_PLUGIN.value,
        "semantic_key": identity.semantic_key,
        "incarnation_id": identity.incarnation_id,
        "artifact_digest": identity.artifact_digest,
        "managed_path": str(identity.managed_path),
        "manifest_path": str(identity.manifest_path),
    }


def read_installed_plugin_artifact_identity(
    managed_path: Path,
    *,
    expected_semantic_key: str | None = None,
    manifest_path: Path | None = None,
) -> PluginArtifactIdentity:
    """Validate one installed artifact using the launch-time identity contract."""
    supplied_root = Path(managed_path)
    if not supplied_root.is_absolute():
        raise PluginArtifactValidationError(
            f"installed plugin root must be absolute: {supplied_root}"
        )
    try:
        canonical_root = supplied_root.resolve(strict=True)
        root_stat = canonical_root.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin root is unavailable: {supplied_root}"
        ) from exc
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"installed plugin root cannot be read: {supplied_root}"
        ) from exc
    if supplied_root != canonical_root or not stat.S_ISDIR(root_stat.st_mode):
        raise PluginArtifactValidationError(
            f"installed plugin root must be a canonical directory: {supplied_root}"
        )

    canonical_manifest = installed_plugin_artifact_manifest_path(canonical_root)
    selected_manifest = canonical_manifest if manifest_path is None else Path(manifest_path)
    if selected_manifest != canonical_manifest:
        raise PluginArtifactValidationError(
            f"installed plugin manifest path is not canonical: {selected_manifest}"
        )
    try:
        manifest_stat = selected_manifest.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is missing: {selected_manifest}"
        ) from exc
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"installed plugin incarnation manifest cannot be read: {selected_manifest}"
        ) from exc
    if not stat.S_ISREG(manifest_stat.st_mode):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is not a regular file: {selected_manifest}"
        )

    try:
        raw = read_versioned_json(
            selected_manifest,
            INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            raise_io_errors=True,
        )
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"installed plugin incarnation manifest cannot be read: {selected_manifest}"
        ) from exc
    if raw is None:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is missing or invalid: {selected_manifest}"
        )
    if frozenset(raw) != INSTALLED_PLUGIN_ARTIFACT_MANIFEST_FIELDS:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest has unexpected fields: {selected_manifest}"
        )
    if (
        type(raw.get("schema_version")) is not int
        or raw["schema_version"] != INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION
    ):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest schema is invalid: {selected_manifest}"
        )
    if raw.get("artifact_kind") != PluginArtifactKind.INSTALLED_PLUGIN.value:
        raise PluginArtifactValidationError(
            f"installed plugin artifact kind is invalid: {selected_manifest}"
        )
    scalar_fields = ("semantic_key", "incarnation_id", "artifact_digest")
    if any(not isinstance(raw.get(field), str) or not raw[field] for field in scalar_fields):
        raise PluginArtifactValidationError(
            "installed plugin incarnation manifest has invalid identity fields: "
            f"{selected_manifest}"
        )
    if not is_canonical_plugin_artifact_incarnation_id(raw["incarnation_id"]):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation is not canonical uuid4 hex: {selected_manifest}"
        )
    if not is_canonical_plugin_artifact_digest(raw["artifact_digest"]):
        raise PluginArtifactValidationError(
            f"installed plugin artifact digest is invalid: {selected_manifest}"
        )
    if expected_semantic_key is not None and raw["semantic_key"] != expected_semantic_key:
        raise PluginArtifactValidationError(
            "installed plugin semantic identity does not match the current transaction"
        )
    if raw.get("managed_path") != str(canonical_root):
        raise PluginArtifactValidationError("installed plugin managed path identity mismatch")
    if raw.get("manifest_path") != str(canonical_manifest):
        raise PluginArtifactValidationError("installed plugin manifest path identity mismatch")
    try:
        observed_digest = directory_tree_digest(canonical_root)
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"installed plugin artifact cannot be read for digest: {canonical_root}"
        ) from exc
    except ValueError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin artifact cannot be digested: {canonical_root}"
        ) from exc
    if raw["artifact_digest"] != observed_digest:
        raise PluginArtifactValidationError("installed plugin content digest mismatch")
    return PluginArtifactIdentity(
        semantic_key=raw["semantic_key"],
        incarnation_id=raw["incarnation_id"],
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=raw["artifact_digest"],
        managed_path=canonical_root,
        manifest_path=canonical_manifest,
    )
