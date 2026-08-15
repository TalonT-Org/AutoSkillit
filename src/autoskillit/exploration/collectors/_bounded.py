"""Fail-closed filesystem and subprocess primitives for repository collectors.

This module deliberately exposes observations only.  It never imports, compiles, or
executes repository code, and every operation is rooted at a verified repository
directory.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final


class CollectorSafetyError(ValueError):
    """Raised when a collector request cannot be performed safely."""


class CollectorMutationError(CollectorSafetyError):
    """Raised when a descriptor-backed observation changes while being read."""


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


@dataclass(frozen=True, slots=True)
class StableContainedFileRead:
    """Bytes and metadata from one stable descriptor-relative file read."""

    content: bytes
    size: int
    mode: int


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
_SUPPORTS_NOFOLLOW_DIRECTORY_OPEN: Final = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
)
_SUPPORTS_DIRECTORY_FD_SCANDIR: Final = os.scandir in os.supports_fd


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    """Return whether two observations name the same inode and file type."""

    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
    )


def _stable_file_metadata(observation: os.stat_result) -> tuple[int, int, int, int]:
    return (
        observation.st_size,
        observation.st_mode,
        observation.st_mtime_ns,
        observation.st_ctime_ns,
    )


def _require_directory_descriptor_support(*, scanning: bool = False) -> None:
    supported = _SUPPORTS_NOFOLLOW_DIRECTORY_OPEN
    if scanning:
        supported = supported and _SUPPORTS_DIRECTORY_FD_SCANDIR
    if not supported:
        raise CollectorSafetyError("collector platform lacks no-follow descriptor support")


def _open_verified_root_directory(root: Path, *, scanning: bool = False) -> int:
    _require_directory_descriptor_support(scanning=scanning)
    try:
        before = root.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise CollectorSafetyError("collector root must be a real directory")
        root_fd = os.open(root, _OPEN_DIRECTORY_FLAGS)
    except CollectorSafetyError:
        raise
    except OSError as exc:
        raise CollectorSafetyError("collector root must be a real directory") from exc

    try:
        opened = os.fstat(root_fd)
        after = root.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_inode(before, opened)
            or not _same_inode(opened, after)
        ):
            raise CollectorSafetyError("collector root changed while opening")
    except CollectorSafetyError:
        os.close(root_fd)
        raise
    except OSError as exc:
        os.close(root_fd)
        raise CollectorSafetyError("collector root changed while opening") from exc
    return root_fd


def _open_verified_directory_at(
    parent_fd: int,
    component: str,
    *,
    expected: os.stat_result | None,
    invalid_message: str,
    changed_message: str,
) -> int:
    before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise CollectorSafetyError(invalid_message)
    if expected is not None and not _same_inode(expected, before):
        raise CollectorSafetyError(changed_message)

    child_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        opened = os.fstat(child_fd)
        after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_inode(before, opened)
            or not _same_inode(opened, after)
        ):
            raise CollectorSafetyError(changed_message)
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _contained_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or "\x00" in relative_path or "\\" in relative_path:
        raise CollectorSafetyError("path must be a non-empty contained relative path")
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or ".git" in relative.parts
    ):
        raise CollectorSafetyError("path must be a non-empty contained relative path")
    return relative.parts


def open_contained_regular_file(root: Path, relative_path: str) -> int:
    """Open a contained regular file without following a mutable path component.

    The caller owns the returned descriptor and must close it.
    Every directory is held by descriptor before the next component is resolved.
    The final path entry is checked before and after the no-follow open, then compared
    with the opened descriptor.  A swap therefore fails closed rather than changing
    what a collector reads or hashes.
    """

    parts = _contained_parts(relative_path)
    parent_fd = _open_verified_root_directory(root)

    file_fd: int | None = None
    handed_to_caller = False
    try:
        for component in parts[:-1]:
            child_fd = _open_verified_directory_at(
                parent_fd,
                component,
                expected=None,
                invalid_message="requested path must stay within collector root",
                changed_message="requested path changed while opening",
            )
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
    parts = _contained_parts(relative_path)
    candidate = root.joinpath(*parts)
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
    artifact_fd = open_contained_regular_file(root, relative_path)
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


def read_stable_contained_file(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> StableContainedFileRead:
    """Read one regular file and reject any path or metadata change during the read."""

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise CollectorSafetyError("requested artifact byte limit must be positive")
    parts = _contained_parts(relative_path)
    parent_fd = _open_verified_root_directory(root)
    file_fd: int | None = None
    target_observed = False
    try:
        for component in parts[:-1]:
            child_fd = _open_verified_directory_at(
                parent_fd,
                component,
                expected=None,
                invalid_message="requested path must stay within collector root",
                changed_message="requested path changed while opening",
            )
            os.close(parent_fd)
            parent_fd = child_fd

        name = parts[-1]
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        target_observed = True
        if not stat.S_ISREG(before.st_mode):
            raise CollectorSafetyError("requested path must be a non-symlink regular file")
        file_fd = os.open(name, _OPEN_REGULAR_FLAGS, dir_fd=parent_fd)
        opened_before = os.fstat(file_fd)
        path_opened = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or not _same_inode(before, opened_before)
            or not _same_inode(opened_before, path_opened)
        ):
            raise CollectorMutationError("requested artifact changed while opening")
        if opened_before.st_size > max_bytes:
            raise CollectorSafetyError("requested artifact exceeds collector byte limit")

        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(file_fd, min(_READ_CHUNK_BYTES, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > max_bytes:
            raise CollectorSafetyError("requested artifact exceeds collector byte limit")

        opened_after = os.fstat(file_fd)
        path_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not _same_inode(opened_before, opened_after)
            or not _same_inode(opened_after, path_after)
            or not (
                _stable_file_metadata(before)
                == _stable_file_metadata(opened_before)
                == _stable_file_metadata(path_opened)
                == _stable_file_metadata(opened_after)
                == _stable_file_metadata(path_after)
            )
            or len(payload) != opened_after.st_size
        ):
            raise CollectorMutationError("requested artifact changed while reading")
        return StableContainedFileRead(
            content=bytes(payload),
            size=opened_after.st_size,
            mode=stat.S_IMODE(opened_after.st_mode),
        )
    except CollectorMutationError:
        raise
    except CollectorSafetyError as exc:
        if "changed while" in str(exc):
            raise CollectorMutationError(str(exc)) from exc
        raise
    except FileNotFoundError as exc:
        if target_observed:
            raise CollectorMutationError("requested artifact changed while reading") from exc
        raise CollectorSafetyError("requested artifact is unavailable") from exc
    except OSError as exc:
        raise CollectorSafetyError("requested artifact cannot be read safely") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def list_contained_files(root: Path, limits: CollectorLimits) -> tuple[str, ...]:
    """List regular non-symlink files beneath ``root`` in deterministic order."""

    root_fd = _open_verified_root_directory(root, scanning=True)
    files: list[str] = []
    pending: list[tuple[tuple[str, os.stat_result], ...]] = [()]
    inspected_entries = 0
    try:
        while pending:
            directory_chain = pending.pop()
            directory_fd = root_fd
            owns_directory_fd = False
            try:
                for component, expected in directory_chain:
                    child_fd = _open_verified_directory_at(
                        directory_fd,
                        component,
                        expected=expected,
                        invalid_message="collector entry cannot be inspected",
                        changed_message="collector entry cannot be inspected",
                    )
                    previous_fd = directory_fd
                    previous_fd_was_owned = owns_directory_fd
                    directory_fd = child_fd
                    owns_directory_fd = True
                    if previous_fd_was_owned:
                        os.close(previous_fd)

                try:
                    with os.scandir(directory_fd) as scanner:
                        entries = sorted(scanner, key=lambda entry: entry.name, reverse=True)
                except OSError as exc:
                    raise CollectorSafetyError("collector root cannot be enumerated") from exc

                for entry in entries:
                    inspected_entries += 1
                    if inspected_entries > limits.max_files:
                        raise CollectorSafetyError("collector entry limit exceeded")
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise CollectorSafetyError("collector entry cannot be inspected") from exc
                    if stat.S_ISLNK(entry_stat.st_mode):
                        continue
                    relative_parts = tuple(
                        component for component, _expected in directory_chain
                    ) + (entry.name,)
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append((*directory_chain, (entry.name, entry_stat)))
                    elif stat.S_ISREG(entry_stat.st_mode):
                        files.append(PurePosixPath(*relative_parts).as_posix())
                        if len(files) > limits.max_files:
                            raise CollectorSafetyError("collector file limit exceeded")
            except OSError as exc:
                raise CollectorSafetyError("collector entry cannot be inspected") from exc
            finally:
                if owns_directory_fd:
                    os.close(directory_fd)
    finally:
        os.close(root_fd)
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
    rg_path = shutil.which("rg")
    if rg_path is None:
        return BoundedCommandResult(None, b"", b"", "rg unavailable (FileNotFoundError)")
    try:
        resolved_rg = Path(rg_path).resolve(strict=True)
        resolved_rg.relative_to(root.resolve(strict=True))
    except ValueError:
        pass
    except OSError as exc:
        return BoundedCommandResult(None, b"", b"", f"rg unavailable ({type(exc).__name__})")
    else:
        return BoundedCommandResult(None, b"", b"", "rg unavailable (untrusted repository path)")
    command = [
        str(resolved_rg),
        "--no-config",
        "--no-follow",
        "--json",
        "--color=never",
        pattern,
    ]
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
        if (
            process.returncode is None
            and process.pid > 0
            and os.getpgid(process.pid) == process.pid
        ):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
