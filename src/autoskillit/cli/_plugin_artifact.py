"""Installed-plugin incarnation publication and launch-time authority."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    DIRECT_INSTALL_CACHE_SUBDIR,
    ArtifactLease,
    PluginArtifactIdentity,
    PluginArtifactPublicationError,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    PluginLoadMode,
    read_versioned_json,
    write_versioned_json,
)

_SCHEMA_VERSION = 1
_ARTIFACT_KIND = "installed_plugin"
_MANIFEST_FIELDS = frozenset(
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

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, PluginArtifactAuthority
    from autoskillit.workspace import EffectiveSkillCatalog


def installed_artifact_manifest_path(root: Path) -> Path:
    """Return the stable external sibling manifest for an installed root."""
    root = Path(root)
    return root.parent / f".{root.name}.autoskillit-artifact.json"


def installed_artifact_lock_path(root: Path) -> Path:
    """Return the stable lease sidecar, which is never retired with the root."""
    manifest_path = installed_artifact_manifest_path(root)
    return manifest_path.with_suffix(manifest_path.suffix + ".lock")


def installed_plugin_semantic_key(plugin_ref: str, version: str) -> str:
    """Bind the installed artifact to the exact plugin/version transaction."""
    if not plugin_ref or not version:
        raise ValueError("installed plugin reference and version must not be empty")
    return f"{plugin_ref}:{version}"


def current_installed_plugin_root() -> Path:
    """Return the cache root created by the current install transaction."""
    from autoskillit import __version__

    return (
        Path.home()
        / ".claude"
        / "plugins"
        / "cache"
        / DIRECT_INSTALL_CACHE_SUBDIR
        / "autoskillit"
        / __version__
    ).resolve(strict=False)


def current_installed_plugin_authority() -> InstalledPluginArtifactAuthority:
    """Build the lazy authority for the currently installed release."""
    from autoskillit import __version__
    from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY

    return InstalledPluginArtifactAuthority(
        current_installed_plugin_root(),
        semantic_key=installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, __version__),
    )


def interactive_plugin_authority(
    *,
    backend: CodingAgentBackend,
    project_dir: Path,
    default_base_branch: str,
    skill_catalog: EffectiveSkillCatalog | None,
    generated_home: Path | None,
) -> tuple[PluginArtifactAuthority | None, PluginLoadMode]:
    """Select authority only after the effective backend and load path are known."""
    from autoskillit.core import MARKETPLACE_PREFIX, detect_autoskillit_mcp_prefix

    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        return None, PluginLoadMode.NONE
    if not capabilities.plugin_install_capable and generated_home is not None:
        return None, PluginLoadMode.GENERATED_HOME
    if capabilities.plugin_install_capable and (
        detect_autoskillit_mcp_prefix(capabilities) == MARKETPLACE_PREFIX
    ):
        return current_installed_plugin_authority(), PluginLoadMode.IMPLICIT_INSTALLED

    from autoskillit.workspace import project_default_plugin_authority

    authority = project_default_plugin_authority(
        base_branch=default_base_branch,
        catalog=skill_catalog,
        cwd=project_dir,
    )
    load_mode = (
        PluginLoadMode.EXPLICIT_PLUGIN_DIR
        if capabilities.plugin_install_capable
        else PluginLoadMode.PROJECTED_HOME
    )
    return authority, load_mode


def publish_installed_plugin_artifact(
    root: Path,
    *,
    semantic_key: str,
) -> PluginArtifactIdentity:
    """Persist a new exact identity after a successful plugin installation."""
    try:
        managed_path = _canonical_installed_root(root)
        manifest_path = installed_artifact_manifest_path(managed_path)
        if not manifest_path.is_absolute():
            raise PluginArtifactPublicationError(
                f"installed plugin manifest path is not absolute: {manifest_path}"
            )
        with ArtifactLease.acquire_exclusive(
            installed_artifact_lock_path(managed_path),
            blocking=True,
        ):
            identity = PluginArtifactIdentity(
                semantic_key=semantic_key,
                incarnation_id=uuid.uuid4().hex,
                manifest_schema_version=_SCHEMA_VERSION,
                artifact_digest=_complete_tree_digest(managed_path),
                managed_path=managed_path,
                manifest_path=manifest_path,
            )
            write_versioned_json(
                manifest_path,
                {
                    "artifact_kind": _ARTIFACT_KIND,
                    "semantic_key": identity.semantic_key,
                    "incarnation_id": identity.incarnation_id,
                    "artifact_digest": identity.artifact_digest,
                    "managed_path": str(identity.managed_path),
                    "manifest_path": str(identity.manifest_path),
                },
                schema_version=_SCHEMA_VERSION,
            )
            return identity
    except PluginArtifactPublicationError:
        raise
    except BaseException as exc:
        raise PluginArtifactPublicationError(
            f"failed to publish installed plugin artifact at {root}: {exc}"
        ) from exc


class InstalledPluginArtifactAuthority:
    """Fail-closed authority for one installed plugin transaction identity."""

    def __init__(self, root: Path, *, semantic_key: str) -> None:
        self._root = Path(root)
        self._semantic_key = semantic_key

    def acquire_launch_binding(
        self,
        *,
        backend: CodingAgentBackend,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding:
        """Acquire a shared reader lease, then validate the exact incarnation."""
        del backend
        if load_mode is not PluginLoadMode.IMPLICIT_INSTALLED:
            raise PluginArtifactValidationError(
                "installed plugin authority requires implicit_installed load mode"
            )
        try:
            managed_path = _canonical_installed_root(self._root)
        except BaseException as exc:
            raise PluginArtifactValidationError(
                f"installed plugin root is invalid: {self._root}"
            ) from exc
        try:
            lease = ArtifactLease.acquire_shared(installed_artifact_lock_path(managed_path))
        except BaseException as exc:
            raise PluginArtifactPublicationError(
                f"installed plugin reader lease acquisition failed: {self._semantic_key}"
            ) from exc
        try:
            identity = _read_and_validate_identity(
                managed_path,
                expected_semantic_key=self._semantic_key,
            )
            return PluginLaunchBinding(
                load_mode=load_mode,
                plugin_dir=None,
                identity=identity,
                inherited_fds=lease.inherited_fds,
                _lease=lease,
            )
        except BaseException:
            lease.close()
            raise


def _read_and_validate_identity(
    managed_path: Path,
    *,
    expected_semantic_key: str,
) -> PluginArtifactIdentity:
    manifest_path = installed_artifact_manifest_path(managed_path)
    try:
        manifest_stat = manifest_path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is missing: {manifest_path}"
        ) from exc
    if not stat.S_ISREG(manifest_stat.st_mode):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is not a regular file: {manifest_path}"
        )
    raw = read_versioned_json(manifest_path, _SCHEMA_VERSION)
    if raw is None:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest is missing or invalid: {manifest_path}"
        )
    if frozenset(raw) != _MANIFEST_FIELDS:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest has unexpected fields: {manifest_path}"
        )
    if (
        isinstance(raw.get("schema_version"), bool)
        or not isinstance(raw.get("schema_version"), int)
        or raw["schema_version"] != _SCHEMA_VERSION
    ):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest schema is invalid: {manifest_path}"
        )
    if raw.get("artifact_kind") != _ARTIFACT_KIND:
        raise PluginArtifactValidationError(
            f"installed plugin artifact kind is invalid: {manifest_path}"
        )
    scalar_fields = ("semantic_key", "incarnation_id", "artifact_digest")
    if any(not isinstance(raw.get(field), str) or not raw[field] for field in scalar_fields):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation manifest has invalid identity fields: {manifest_path}"
        )
    incarnation_id = raw["incarnation_id"]
    try:
        incarnation_uuid = uuid.UUID(hex=incarnation_id)
    except ValueError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin incarnation is invalid: {manifest_path}"
        ) from exc
    if (
        len(incarnation_id) != 32
        or incarnation_uuid.hex != incarnation_id
        or incarnation_uuid.version != 4
    ):
        raise PluginArtifactValidationError(
            f"installed plugin incarnation is not canonical uuid4 hex: {manifest_path}"
        )
    artifact_digest = raw["artifact_digest"]
    if (
        len(artifact_digest) != 64
        or artifact_digest.lower() != artifact_digest
        or any(character not in "0123456789abcdef" for character in artifact_digest)
    ):
        raise PluginArtifactValidationError(
            f"installed plugin artifact digest is invalid: {manifest_path}"
        )
    if raw["semantic_key"] != expected_semantic_key:
        raise PluginArtifactValidationError(
            "installed plugin semantic identity does not match the current transaction"
        )
    if raw.get("managed_path") != str(managed_path):
        raise PluginArtifactValidationError("installed plugin managed path identity mismatch")
    if raw.get("manifest_path") != str(manifest_path):
        raise PluginArtifactValidationError("installed plugin manifest path identity mismatch")
    observed_digest = _complete_tree_digest(managed_path)
    if raw["artifact_digest"] != observed_digest:
        raise PluginArtifactValidationError("installed plugin content digest mismatch")
    return PluginArtifactIdentity(
        semantic_key=raw["semantic_key"],
        incarnation_id=raw["incarnation_id"],
        manifest_schema_version=_SCHEMA_VERSION,
        artifact_digest=raw["artifact_digest"],
        managed_path=managed_path,
        manifest_path=manifest_path,
    )


def _canonical_installed_root(root: Path) -> Path:
    supplied = Path(root)
    if not supplied.is_absolute():
        raise ValueError(f"installed plugin root must be absolute: {supplied}")
    resolved = supplied.resolve(strict=True)
    if supplied != resolved:
        raise ValueError(f"installed plugin root must already be canonical: {supplied}")
    root_stat = resolved.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"installed plugin root must be a directory: {resolved}")
    return resolved


def _complete_tree_digest(root: Path) -> str:
    """Hash every relative entry, kind, mode, and regular-file byte."""
    digest = hashlib.sha256()
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        directory_names.sort()
        file_names.sort()
        for name in (*directory_names, *file_names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            entry_stat = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise PluginArtifactValidationError(
                    f"installed plugin artifact contains a symlink: {path}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                kind = b"d"
            elif stat.S_ISREG(entry_stat.st_mode):
                kind = b"f"
            else:
                raise PluginArtifactValidationError(
                    f"installed plugin artifact contains a special file: {path}"
                )
            digest.update(kind)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(stat.S_IMODE(entry_stat.st_mode).to_bytes(2, "big"))
            if kind == b"f":
                with path.open("rb") as handle:
                    digest.update(hashlib.file_digest(handle, "sha256").digest())
            digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "InstalledPluginArtifactAuthority",
    "current_installed_plugin_authority",
    "current_installed_plugin_root",
    "installed_artifact_lock_path",
    "installed_artifact_manifest_path",
    "installed_plugin_semantic_key",
    "interactive_plugin_authority",
    "publish_installed_plugin_artifact",
]
