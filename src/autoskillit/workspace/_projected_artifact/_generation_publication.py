"""Generation-keyed publication for plugin artifacts.

Stages a fresh generation at an incarnation-keyed immutable path, then
flips an atomic ``current`` symlink to select it. The flip is the sole
commit point — nothing is written into or verified against the generation
after it. Publication never contends with readers because the fresh
directory is unpublished until the flip.

Co-located in ``workspace/_projected_artifact/`` per its AGENTS.md.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from autoskillit.core import (
    PluginArtifactIdentity,
    PluginArtifactKind,
    directory_tree_digest,
    generation_artifact_root,
    generation_selector_path,
    generation_version_root,
    get_logger,
    installed_plugin_artifact_manifest_path,
    log_plugin_artifact_lifecycle,
    new_plugin_artifact_incarnation_id,
    resolve_current_generation,
)
from autoskillit.workspace._installed_artifact import (
    write_installed_plugin_artifact_manifest_locked,
)

logger = get_logger(__name__)


def _fsync_directory(path: Path) -> None:
    """Fsync a directory for durability after renames."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_symlink(path: Path, target: Path) -> None:
    """Atomically replace a symlink via temp-link + os.replace + fsync."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.link")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    temporary.symlink_to(target)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_tree_contents(root: Path) -> None:
    """Fsync every regular file and directory entry under *root*."""
    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        for name in files:
            fpath = current / name
            fd = os.open(fpath, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_directory(current)


def publish_generation(
    *,
    home: Path,
    plugin_ref: str,
    version: str,
    semantic_key: str,
    source_root: Path,
) -> PluginArtifactIdentity:
    """Stage a fresh generation, verify, flip the selector, and enqueue the prior.

    Must be called under ``_InstallLock`` by the caller (not acquired here,
    so the caller can compose multiple operations under one lock).

    Returns the identity of the newly published generation.
    """
    incarnation_id = new_plugin_artifact_incarnation_id()
    version_root = generation_version_root(home, plugin_ref, version)
    generation_root = generation_artifact_root(home, plugin_ref, version, incarnation_id)
    selector = generation_selector_path(home, plugin_ref, version)

    version_root.mkdir(parents=True, exist_ok=True)

    # Stage: copy source into the generation directory
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{incarnation_id}.staging-",
            dir=version_root,
        )
    )
    try:
        # Copy the entire source tree
        shutil.copytree(source_root, staging / "content", dirs_exist_ok=False)
        content_root = staging / "content"

        # Write manifest with the caller-supplied incarnation_id
        identity = write_installed_plugin_artifact_manifest_locked(
            content_root,
            semantic_key=semantic_key,
            action="publish_generation",
            incarnation_id=incarnation_id,
        )

        # Verify digest
        observed = directory_tree_digest(content_root)
        if observed != identity.artifact_digest:
            raise RuntimeError(
                f"staged generation digest verification failed: "
                f"expected {identity.artifact_digest}, got {observed}"
            )

        # Fsync staged contents for durability
        _fsync_tree_contents(content_root)

        # Move staging content to the final generation path
        os.rename(staging / "content", generation_root)
        _fsync_directory(version_root)

    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Clean up the empty staging dir
    try:
        staging.rmdir()
    except OSError:
        pass

    # Record prior target for rollback
    prior_target = resolve_current_generation(home, plugin_ref, version)

    # Flip the selector — the sole commit point
    try:
        _replace_symlink(selector, generation_root)
    except OSError:
        # Flip failed — restore prior if we had one
        if prior_target is not None:
            try:
                _replace_symlink(selector, prior_target)
            except OSError as restore_error:
                logger.error(
                    "generation_selector_restore_failed: prior=%s",
                    prior_target,
                    exc_info=restore_error,
                )
        raise

    log_plugin_artifact_lifecycle(
        logger,
        action="publish_generation",
        outcome="succeeded",
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN.value,
        semantic_key=semantic_key,
        incarnation=incarnation_id,
    )

    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        manifest_schema_version=identity.manifest_schema_version,
        artifact_digest=identity.artifact_digest,
        managed_path=generation_root,
        manifest_path=installed_plugin_artifact_manifest_path(generation_root),
    )
