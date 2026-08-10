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

__all__ = [
    "ContainmentError",
    "check_metadata_stable",
    "read_stable_contained_range",
    "read_stable_contained_bytes",
    "resolve_contained_path",
]


class ContainmentError(Exception):
    """Raised when a path fails containment validation."""

    def __init__(self, message: str, *, reason: str = "containment_error") -> None:
        super().__init__(message)
        self.reason = reason


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
    if not stat.S_ISREG(st.st_mode):
        raise ContainmentError("Regular file required")
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
    if pre_stat.st_dev != post_stat.st_dev:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")
    if pre_stat.st_mode != post_stat.st_mode:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")
    if pre_stat.st_nlink != post_stat.st_nlink:
        raise ContainmentError(f"File {path} modified between reads (TOCTOU)")


def _open_beneath_root_without_symlinks(
    path: str | Path,
    allowed_root: str | Path,
    resolved: Path,
) -> int:
    """Open a child through a trusted root descriptor without following symlinks."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ContainmentError(
            "Secure component-wise open is unavailable",
            reason="secure_open_unavailable",
        )

    resolved_root = Path(allowed_root).resolve(strict=True)
    try:
        relative = Path(path).absolute().relative_to(Path(allowed_root).absolute())
    except ValueError:
        relative = resolved.relative_to(resolved_root)
    if not relative.parts:
        raise ContainmentError("Regular file required")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_fds: list[int] = []
    try:
        directory_fds.append(os.open(resolved_root, directory_flags))
        for component in relative.parts[:-1]:
            directory_fds.append(os.open(component, directory_flags, dir_fd=directory_fds[-1]))
        return os.open(relative.parts[-1], file_flags, dir_fd=directory_fds[-1])
    except OSError as exc:
        raise ContainmentError("Symlink or unsafe path component not allowed") from exc
    finally:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def read_stable_contained_bytes(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = 50_000_000,
) -> tuple[Path, bytes]:
    """Read one bounded buffer while detecting containment and metadata drift."""
    resolved = resolve_contained_path(path, allowed_root, max_size_bytes=max_size_bytes)
    pre_stat = resolved.stat()
    fd = _open_beneath_root_without_symlinks(path, allowed_root, resolved)
    try:
        opened_stat = os.fstat(fd)
        check_metadata_stable(resolved, pre_stat, opened_stat)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max_size_bytes + 1)
        post_fd_stat = os.fstat(fd)
    finally:
        os.close(fd)
    if len(data) > max_size_bytes:
        raise ContainmentError("File too large")
    if len(data) != pre_stat.st_size:
        raise ContainmentError(f"File {resolved} modified between reads (TOCTOU)")
    check_metadata_stable(resolved, pre_stat, post_fd_stat)
    check_metadata_stable(resolved, pre_stat, resolved.stat())
    return resolved, data


def read_stable_contained_range(
    path: str | Path,
    allowed_root: str | Path,
    *,
    offset: int,
    length: int,
    max_range_bytes: int = 1_000_000,
) -> tuple[Path, bytes, os.stat_result]:
    """Read a descriptor-bound byte range from the file's opening snapshot."""
    if offset < 0:
        raise ContainmentError("Range offset must be non-negative", reason="range_invalid")
    if length < 0 or length > max_range_bytes:
        raise ContainmentError("Range length is invalid", reason="range_invalid")

    resolved = resolve_contained_path(path, allowed_root, max_size_bytes=2**63 - 1)
    pre_open_stat = resolved.stat()
    fd = _open_beneath_root_without_symlinks(path, allowed_root, resolved)
    try:
        opening_stat = os.fstat(fd)
        _check_range_identity(resolved, pre_open_stat, opening_stat)
        read_length = min(length, max(0, opening_stat.st_size - offset))
        if hasattr(os, "pread"):
            data = os.pread(fd, read_length, offset)
        else:
            os.lseek(fd, offset, os.SEEK_SET)
            data = os.read(fd, read_length)
        post_fd_stat = os.fstat(fd)
    finally:
        os.close(fd)

    post_path_stat = resolved.stat()
    _check_range_snapshot(resolved, opening_stat, post_fd_stat)
    _check_range_snapshot(resolved, opening_stat, post_path_stat)
    if len(data) > read_length:
        raise ContainmentError("Range read exceeded requested length", reason="range_unstable")
    return resolved, data, opening_stat


def _check_range_identity(
    path: Path,
    expected: os.stat_result,
    actual: os.stat_result,
) -> None:
    if (
        expected.st_dev != actual.st_dev
        or expected.st_ino != actual.st_ino
        or expected.st_mode != actual.st_mode
        or expected.st_nlink != actual.st_nlink
    ):
        raise ContainmentError(f"File {path} identity changed", reason="range_unstable")


def _check_range_snapshot(
    path: Path,
    opening: os.stat_result,
    current: os.stat_result,
) -> None:
    _check_range_identity(path, opening, current)
    if current.st_size < opening.st_size:
        raise ContainmentError(f"File {path} shrank during read", reason="range_unstable")
    if current.st_size == opening.st_size and current.st_mtime_ns != opening.st_mtime_ns:
        raise ContainmentError(f"File {path} modified during read", reason="range_unstable")
