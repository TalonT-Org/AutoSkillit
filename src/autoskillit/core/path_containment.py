"""Path containment guards for closure-mode artifact validation (IL-0, stdlib-only).

Resolves a child path against an already-trusted allowed root, rejecting child
symlinks, hardlinks, traversal escapes, oversized files, and world-writable
files. Root authority is a caller precondition; this module does not establish
whether ``allowed_root`` itself or its ancestors are hostile. Also provides a
metadata-stability check for TOCTOU detection between pre- and post-read stats.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["ContainmentError", "resolve_contained_path", "check_metadata_stable"]


class ContainmentError(Exception):
    """Raised when a path fails containment validation."""


def resolve_contained_path(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = 50_000_000,
) -> Path:
    """Validate a child path beneath an allowed root whose authority is pre-established."""

    original = Path(path)
    orig_st = original.lstat()
    if stat.S_ISLNK(orig_st.st_mode):
        raise ContainmentError("Symlink not allowed")
    allowed_root_resolved = Path(allowed_root).resolve(strict=True)
    resolved = original.resolve(strict=True)
    if not resolved.is_relative_to(allowed_root_resolved):
        raise ContainmentError("Path escapes allowed root")
    st = resolved.stat()
    if st.st_nlink > 1:
        raise ContainmentError("Hardlink not allowed")
    if st.st_size > max_size_bytes:
        raise ContainmentError("File too large")
    if st.st_mode & 0o002:
        raise ContainmentError("World-writable file")
    return resolved


def check_metadata_stable(path: Path, pre_stat: os.stat_result, post_stat: os.stat_result) -> None:
    if pre_stat.st_mtime_ns != post_stat.st_mtime_ns:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")
    if pre_stat.st_size != post_stat.st_size:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")
    if pre_stat.st_ino != post_stat.st_ino:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")
