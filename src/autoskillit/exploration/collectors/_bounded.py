"""Fail-closed filesystem and subprocess primitives for repository collectors.

This module deliberately exposes observations only.  It never imports, compiles, or
executes repository code, and every operation is rooted at a verified repository
directory.
"""

from __future__ import annotations

import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final


class CollectorSafetyError(ValueError):
    """Raised when a collector request cannot be performed safely."""


@dataclass(frozen=True, slots=True)
class CollectorLimits:
    """Hard resource limits shared by all observational collectors."""

    max_files: int = 2_000
    max_file_bytes: int = 1_000_000
    max_output_bytes: int = 1_000_000
    max_matches: int = 1_000
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            self.max_files <= 0
            or self.max_file_bytes <= 0
            or self.max_output_bytes <= 0
            or self.max_matches <= 0
            or self.timeout_seconds <= 0
        ):
            raise ValueError("collector limits must be positive")


@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    """A bounded, non-shell command observation."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    failure: str | None = None


_READ_CHUNK_BYTES: Final = 64 * 1024
_SAFE_RG_ENV: Final = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
_OPEN_DIRECTORY_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_REGULAR_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    """Return whether two observations name the same inode and file type."""

    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
    )


def _contained_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or "\x00" in relative_path:
        raise CollectorSafetyError("path must be a non-empty contained relative path")
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CollectorSafetyError("path must be a non-empty contained relative path")
    return relative.parts


def _open_contained_regular_file(root: Path, relative_path: str) -> int:
    """Open a contained regular file without following a mutable path component.

    Every directory is held by descriptor before the next component is resolved.
    The final path entry is checked before and after the no-follow open, then compared
    with the opened descriptor.  A swap therefore fails closed rather than changing
    what a collector reads or hashes.
    """

    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise CollectorSafetyError("collector platform lacks no-follow descriptor support")
    parts = _contained_parts(relative_path)
    try:
        root_before = root.lstat()
        if not stat.S_ISDIR(root_before.st_mode):
            raise CollectorSafetyError("collector root must be a real directory")
        parent_fd = os.open(root, _OPEN_DIRECTORY_FLAGS)
    except CollectorSafetyError:
        raise
    except OSError as exc:
        raise CollectorSafetyError("collector root must be a real directory") from exc

    file_fd: int | None = None
    handed_to_caller = False
    try:
        root_open = os.fstat(parent_fd)
        if not stat.S_ISDIR(root_open.st_mode) or not _same_inode(root_before, root_open):
            raise CollectorSafetyError("collector root changed while opening")

        for component in parts[:-1]:
            before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise CollectorSafetyError("requested path must stay within collector root")
            child_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
            try:
                opened = os.fstat(child_fd)
                after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not _same_inode(before, opened)
                    or not _same_inode(opened, after)
                ):
                    raise CollectorSafetyError("requested path changed while opening")
            except BaseException:
                os.close(child_fd)
                raise
            os.close(parent_fd)
            parent_fd = child_fd

        name = parts[-1]
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise CollectorSafetyError("requested path must be a non-symlink regular file")
        file_fd = os.open(name, _OPEN_REGULAR_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_inode(before, opened)
            or not _same_inode(opened, after)
        ):
            raise CollectorSafetyError("requested path changed while opening")
        handed_to_caller = True
        return file_fd
    except CollectorSafetyError:
        raise
    except OSError as exc:
        raise CollectorSafetyError("requested path is unavailable") from exc
    finally:
        os.close(parent_fd)
        if file_fd is not None and not handed_to_caller:
            os.close(file_fd)


def resolve_contained_path(root: Path, relative_path: str) -> Path:
    """Resolve a relative regular-file path without allowing a containment escape."""

    if not root.is_dir() or root.is_symlink():
        raise CollectorSafetyError("collector root must be a real directory")
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CollectorSafetyError("path must be a non-empty contained relative path")
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as exc:
        raise CollectorSafetyError("requested path is unavailable") from exc
    if resolved_root not in (resolved_candidate, *resolved_candidate.parents):
        raise CollectorSafetyError("requested path escapes collector root")
    try:
        file_stat = candidate.lstat()
    except OSError as exc:
        raise CollectorSafetyError("requested path is unavailable") from exc
    if not stat.S_ISREG(file_stat.st_mode) or candidate.is_symlink():
        raise CollectorSafetyError("requested path must be a non-symlink regular file")
    return candidate


def read_contained_file(root: Path, relative_path: str, limits: CollectorLimits) -> bytes:
    """Read a bounded regular artifact after containment and special-file checks."""

    # Keep the public resolver's precise diagnostics, but do not use its pathname
    # validation as read authority: a replacement after this check is handled by the
    # descriptor-relative open below.
    resolve_contained_path(root, relative_path)
    artifact_fd = _open_contained_regular_file(root, relative_path)
    try:
        size = os.fstat(artifact_fd).st_size
    except OSError as exc:
        os.close(artifact_fd)
        raise CollectorSafetyError("requested artifact is unavailable") from exc
    try:
        if size > limits.max_file_bytes:
            raise CollectorSafetyError("requested artifact exceeds collector byte limit")
        payload = bytearray()
        while len(payload) <= limits.max_file_bytes:
            chunk = os.read(
                artifact_fd,
                min(_READ_CHUNK_BYTES, limits.max_file_bytes + 1 - len(payload)),
            )
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
        raise CollectorSafetyError("requested artifact exceeds collector byte limit")
    except CollectorSafetyError:
        raise
    except OSError as exc:
        raise CollectorSafetyError("requested artifact cannot be read") from exc
    finally:
        os.close(artifact_fd)


def list_contained_files(root: Path, limits: CollectorLimits) -> tuple[str, ...]:
    """List regular non-symlink files beneath ``root`` in deterministic order."""

    if not root.is_dir() or root.is_symlink():
        raise CollectorSafetyError("collector root must be a real directory")
    root = root.resolve(strict=True)
    files: list[str] = []
    pending = [root]
    inspected_entries = 0
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name, reverse=True)
        except OSError as exc:
            raise CollectorSafetyError("collector root cannot be enumerated") from exc
        for entry in entries:
            inspected_entries += 1
            if inspected_entries > limits.max_files:
                raise CollectorSafetyError("collector entry limit exceeded")
            try:
                entry_stat = entry.lstat()
            except OSError as exc:
                raise CollectorSafetyError("collector entry cannot be inspected") from exc
            if entry.is_symlink():
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(entry)
            elif stat.S_ISREG(entry_stat.st_mode):
                files.append(entry.relative_to(root).as_posix())
                if len(files) > limits.max_files:
                    raise CollectorSafetyError("collector file limit exceeded")
    return tuple(sorted(files))


def run_bounded_rg(
    root: Path,
    pattern: str,
    *,
    globs: tuple[str, ...] = (),
    limits: CollectorLimits,
) -> BoundedCommandResult:
    """Run an exact, credential-free ``rg`` observation with hard limits.

    ``rg`` is never invoked through a shell.  Its configuration and symlink following
    are explicitly disabled, and stdout plus stderr share one byte budget.
    """

    if not pattern or "\x00" in pattern:
        raise CollectorSafetyError("ripgrep pattern must be non-empty")
    if not root.is_dir() or root.is_symlink():
        raise CollectorSafetyError("collector root must be a real directory")
    command = ["rg", "--no-config", "--no-follow", "--json", "--color=never", pattern]
    for glob in globs:
        if not glob or "\x00" in glob:
            raise CollectorSafetyError("ripgrep glob is invalid")
        command.extend(("--glob", glob))
    command.append(".")
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=dict(_SAFE_RG_ENV),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return BoundedCommandResult(None, b"", b"", f"rg unavailable ({type(exc).__name__})")
    return _drain_bounded_process(process, limits)


def _drain_bounded_process(
    process: subprocess.Popen[bytes], limits: CollectorLimits
) -> BoundedCommandResult:
    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector_factory = selectors.DefaultSelector
    selector = selector_factory()
    try:
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + limits.timeout_seconds
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                return BoundedCommandResult(
                    None, bytes(output["stdout"]), bytes(output["stderr"]), "timeout"
                )
            for key, _ in selector.select(remaining):
                fileobj = key.fileobj
                file_descriptor = fileobj if isinstance(fileobj, int) else fileobj.fileno()
                chunk = os.read(file_descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if (
                    len(output["stdout"]) + len(output["stderr"]) + len(chunk)
                    > limits.max_output_bytes
                ):
                    _terminate(process)
                    return BoundedCommandResult(
                        None,
                        bytes(output["stdout"]),
                        bytes(output["stderr"]),
                        "output limit exceeded",
                    )
                output[key.data].extend(chunk)
        return BoundedCommandResult(
            process.wait(timeout=0), bytes(output["stdout"]), bytes(output["stderr"])
        )
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
