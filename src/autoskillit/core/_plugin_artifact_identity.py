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


# ---------------------------------------------------------------------------
# Generation store — AutoSkillit-owned immutable-path publication
# ---------------------------------------------------------------------------

_GENERATION_STORE_ROOT = ".autoskillit/plugin-generations"


def generation_store_root(home: Path, plugin_ref: str) -> Path:
    """Return the generation store root for a plugin."""
    plugin_name = plugin_ref.partition("@")[0]
    return Path(home) / _GENERATION_STORE_ROOT / plugin_name


def generation_version_root(home: Path, plugin_ref: str, version: str) -> Path:
    """Return the version directory inside the generation store."""
    return generation_store_root(home, plugin_ref) / version


def generation_artifact_root(
    home: Path, plugin_ref: str, version: str, incarnation_id: str
) -> Path:
    """Return the immutable generation directory for one incarnation."""
    return generation_version_root(home, plugin_ref, version) / incarnation_id


def generation_selector_path(home: Path, plugin_ref: str, version: str) -> Path:
    """Return the ``current`` symlink that selects the active generation."""
    return generation_version_root(home, plugin_ref, version) / "current"


def resolve_current_generation(home: Path, plugin_ref: str, version: str) -> Path | None:
    """Resolve the ``current`` selector to the active generation directory.

    Returns ``None`` when no selector exists or the target is missing.
    """
    selector = generation_selector_path(home, plugin_ref, version)
    if not selector.is_symlink():
        return None
    try:
        version_root = selector.parent.resolve(strict=True)
        target = selector.resolve(strict=True)
    except OSError:
        return None
    return target if target.is_dir() and target.parent == version_root else None


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


def is_python_bytecode_path(path: Path) -> bool:
    """Return whether *path* names interpreter-generated Python bytecode."""
    return path.name == "__pycache__" or path.name.endswith((".pyc", ".pyo"))


def _classify_bytecode_contamination(root: Path) -> str:
    """Scan an artifact tree for bytecode contamination.

    Returns a short description if ``__pycache__``/``*.pyc``/``*.pyo`` are
    found, empty string otherwise. Claims co-occurrence, not causation —
    "digest mismatch with bytecode contamination present."
    """
    bytecode_paths = [path for path in root.rglob("*") if is_python_bytecode_path(path)]
    pycache_dirs = [path for path in bytecode_paths if path.name == "__pycache__"]
    pyc_files = [path for path in bytecode_paths if path.name != "__pycache__"]
    if not pycache_dirs and not pyc_files:
        return ""
    parts: list[str] = []
    if pycache_dirs:
        parts.append(f"{len(pycache_dirs)} __pycache__ dir(s)")
    if pyc_files:
        parts.append(f"{len(pyc_files)} .pyc/.pyo file(s)")
    return ", ".join(parts)


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
    # Generation-store identity cross-check: when the directory name IS a
    # canonical incarnation_id (uuid4 hex), it must match the manifest's
    # incarnation_id — the path IS the identity.  Legacy Claude-cache
    # artifacts use version strings as directory names, so only enforce
    # this for directories that look like incarnation ids.
    if (
        is_canonical_plugin_artifact_incarnation_id(canonical_root.name)
        and raw["incarnation_id"] != canonical_root.name
    ):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation_id {raw['incarnation_id']!r} does not "
            f"match directory name {canonical_root.name!r}"
        )
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
        contamination = _classify_bytecode_contamination(canonical_root)
        if contamination:
            raise PluginArtifactValidationError(
                f"installed plugin content digest mismatch "
                f"(bytecode contamination present: {contamination})"
            )
        raise PluginArtifactValidationError("installed plugin content digest mismatch")
    return PluginArtifactIdentity(
        semantic_key=raw["semantic_key"],
        incarnation_id=raw["incarnation_id"],
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=raw["artifact_digest"],
        managed_path=canonical_root,
        manifest_path=canonical_manifest,
    )
