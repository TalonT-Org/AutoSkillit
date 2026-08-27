"""Crash-safe disposal of an artifact root and its external manifest."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def residue_staging_path(managed_path: Path) -> Path:
    """Return the deterministic residue path for one managed artifact root."""
    managed_path = Path(managed_path)
    suffix = hashlib.sha256(managed_path.name.encode()).hexdigest()[:16]
    return managed_path.parent / f".{managed_path.name}.autoskillit-residue-{suffix}"


def teardown_artifact_residue(*, staging: Path, manifest: Path) -> None:
    """Finish a rename-committed residue transition in crash-safe order."""
    if manifest.exists() or manifest.is_symlink():
        manifest.unlink()
    shutil.rmtree(staging)


def quarantine_artifact_residue(
    *,
    managed_path: Path,
    staging: Path,
    manifest: Path,
) -> None:
    """Move one already-authorized artifact to residue, then tear it down."""
    os.rename(managed_path, staging)
    teardown_artifact_residue(staging=staging, manifest=manifest)
