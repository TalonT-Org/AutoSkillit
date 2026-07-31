"""Runtime resolution and validation for interactive executable bindings."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from ..types import ExecutableLaunchBinding

__all__ = [
    "executable_binding_matches_current_file",
    "resolve_executable_launch_binding",
]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_executable_launch_binding(
    *,
    binary_name: str,
    environment: Mapping[str, str],
    cwd: Path,
    explicit_path_env: str | None = None,
) -> ExecutableLaunchBinding:
    """Resolve and seal the exact executable selected by the effective environment."""
    explicit = environment.get(explicit_path_env, "") if explicit_path_env else ""
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"{explicit_path_env} must be an absolute path")
    else:
        resolved = shutil.which(binary_name, path=environment.get("PATH"))
        if resolved is None:
            raise ValueError(f"'{binary_name}' not found in the effective PATH")
        candidate = Path(resolved)
        if not candidate.exists():
            path_environment = environment if "PATH" in environment else os.environ
            for directory in os.get_exec_path(path_environment):
                effective_candidate = Path(directory) / binary_name
                if effective_candidate.is_file() and os.access(effective_candidate, os.X_OK):
                    candidate = effective_candidate
                    break
    try:
        canonical = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Executable path cannot be resolved: {candidate}") from exc
    if not canonical.is_file() or not os.access(canonical, os.X_OK):
        raise ValueError(f"Executable path is not executable: {canonical}")
    canonical_cwd = cwd.expanduser().resolve(strict=True)
    stat_result = canonical.stat()
    return ExecutableLaunchBinding(
        path=canonical,
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        file_sha256=_sha256_file(canonical),
        cwd=canonical_cwd,
        launch_environment=MappingProxyType(dict(environment)),
    )


def executable_binding_matches_current_file(binding: ExecutableLaunchBinding) -> bool:
    """Return whether the probed executable identity still owns its bound path."""
    try:
        stat_result = binding.path.stat()
        return (
            stat_result.st_dev == binding.device
            and stat_result.st_ino == binding.inode
            and stat_result.st_size == binding.size
            and stat_result.st_mtime_ns == binding.mtime_ns
            and _sha256_file(binding.path) == binding.file_sha256
        )
    except OSError:
        return False
