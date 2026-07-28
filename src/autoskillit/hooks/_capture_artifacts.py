"""Descriptor-anchored shell-capture authority and isolated runner.

This module is stdlib-only and is executable under Python isolated mode. It
owns the trust boundary for capture policy reads, artifact publication, and
replay.  Durable state and reclamation live in ``_capture_lifecycle``.
"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture_contract import _CAPTURE_ID_RE, _MAX_COMMAND_BYTES
    from autoskillit.hooks._capture_lifecycle import (
        CaptureLifecycleError,
        CaptureLifecycleStore,
    )
    from autoskillit.hooks._hook_settings import (
        HOOK_CONFIG_FILENAME,
        HOOK_CONFIG_OVERLAY_FILENAME,
        merge_hook_configs,
    )
    from autoskillit.hooks._policy_event import PolicyEvent, render_capture_marker
else:
    from _capture_contract import _CAPTURE_ID_RE, _MAX_COMMAND_BYTES
    from _capture_lifecycle import CaptureLifecycleError, CaptureLifecycleStore
    from _hook_settings import (
        HOOK_CONFIG_FILENAME,
        HOOK_CONFIG_OVERLAY_FILENAME,
        merge_hook_configs,
    )
    from _policy_event import PolicyEvent, render_capture_marker

__all__ = [
    "CAPTURE_PATH_COMPONENTS",
    "CaptureArtifact",
    "CapturePolicy",
    "CaptureRoot",
    "CaptureSetupError",
    "ProjectAnchor",
    "create_capture_artifact",
    "current_artifact_path_if_bound",
    "open_capture_lifecycle",
    "open_capture_root",
    "open_project_anchor",
    "read_capture_policy",
    "run_capture",
]

CAPTURE_PATH_COMPONENTS = (".autoskillit", "temp", "shell_capture")

_DEFAULT_INLINE_BYTES = 12_000
_MAX_INLINE_BYTES = 1_000_000
_MAX_POLICY_FILE_BYTES = 64 * 1024
_MAX_ENCODED_COMMAND_BYTES = ((_MAX_COMMAND_BYTES + 2) // 3) * 4
_DRAIN_CHUNK_BYTES = 64 * 1024
_CAPTURE_FAILURE_RETURN_CODE = 1
_PROCESS_SETTLE_TIMEOUT_SECONDS = 2
_TRUSTED_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash")
_EXECUTABLE_MODE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_CAPTURE_RUNTIME_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
    TypeError,
    UnicodeError,
    ValueError,
)
_AUTHORITY_FACTORY_TOKEN = object()

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class CaptureSetupError(RuntimeError):
    """Raised when the descriptor-anchored capture authority cannot be established."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(device=value.st_dev, inode=value.st_ino)


@dataclass(frozen=True, slots=True)
class ProjectAnchor:
    """Opened project directory and its post-open physical-path hint."""

    fd: int
    identity: FileIdentity
    supplied_path: str
    physical_path: Path
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise CaptureSetupError("ProjectAnchor must be created by open_project_anchor")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            object.__setattr__(self, "fd", -1)


@dataclass(frozen=True, slots=True)
class CaptureRoot:
    """Opened capture-root chain retained for the capture lifetime."""

    autoskillit_fd: int
    temp_fd: int
    fd: int
    autoskillit_identity: FileIdentity
    temp_identity: FileIdentity
    identity: FileIdentity
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise CaptureSetupError("CaptureRoot must be created by open_capture_root")

    def close(self) -> None:
        for field_name in ("fd", "temp_fd", "autoskillit_fd"):
            fd = getattr(self, field_name)
            if fd >= 0:
                os.close(fd)
                object.__setattr__(self, field_name, -1)


@dataclass(frozen=True, slots=True)
class CaptureArtifact:
    """Exclusive capture artifact retained by descriptor."""

    fd: int
    name: str
    identity: FileIdentity
    lease_fd: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise CaptureSetupError("CaptureArtifact must be created by create_capture_artifact")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            object.__setattr__(self, "fd", -1)

    def release_lease(self) -> None:
        if self.lease_fd >= 0:
            os.close(self.lease_fd)
            object.__setattr__(self, "lease_fd", -1)


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    disabled: bool = False
    inline_bytes: int = _DEFAULT_INLINE_BYTES


@dataclass(frozen=True, slots=True)
class _DrainResult:
    total_bytes: int
    sha256: str
    inline: bytes
    head: bytes
    tail: bytes
    write_error: OSError | None


def _require_capabilities() -> None:
    required_dir_fd = (os.link, os.mkdir, os.open, os.stat, os.unlink)
    required_flags = ("O_CLOEXEC", "O_CREAT", "O_DIRECTORY", "O_EXCL", "O_NOFOLLOW", "O_NONBLOCK")
    if (
        any(getattr(os, flag, 0) == 0 for flag in required_flags)
        or not hasattr(os, "fchdir")
        or not hasattr(os, "fstat")
        or not hasattr(os, "pread")
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
        or os.stat not in getattr(os, "supports_follow_symlinks", ())
        or os.listdir not in getattr(os, "supports_fd", ())
    ):
        raise CaptureSetupError("required descriptor-relative filesystem primitives unavailable")


def _identity(fd: int) -> FileIdentity:
    return FileIdentity.from_stat(os.fstat(fd))


def _same_identity(fd: int, expected: FileIdentity) -> bool:
    return _identity(fd) == expected


def _open_directory_component(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise CaptureSetupError(f"missing capture path component: {name}") from None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise CaptureSetupError(f"cannot create capture path component: {name}") from exc
        try:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise CaptureSetupError(f"cannot open created capture component: {name}") from exc
    except OSError as exc:
        raise CaptureSetupError(f"unsafe capture path component: {name}") from exc

    try:
        value = os.fstat(fd)
        if not stat.S_ISDIR(value.st_mode):
            raise CaptureSetupError(f"capture path component is not a directory: {name}")
        if value.st_uid != os.geteuid() or value.st_mode & _UNTRUSTED_WRITE_BITS:
            raise CaptureSetupError(f"capture path component has unsafe ownership or mode: {name}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_project_anchor(cwd: str) -> ProjectAnchor:
    """Open the supplied cwd first; a symlink in the supplied spelling is allowed."""

    _require_capabilities()
    if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd) or "\x00" in cwd:
        raise CaptureSetupError("cwd must be a non-empty absolute path")
    try:
        fd = os.open(cwd, _DIRECTORY_FLAGS & ~getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CaptureSetupError("cannot open project anchor") from exc
    try:
        anchor_stat = os.fstat(fd)
        if not stat.S_ISDIR(anchor_stat.st_mode):
            raise CaptureSetupError("project anchor is not a directory")
        physical_path = Path(os.path.realpath(cwd))
        return ProjectAnchor(
            fd=fd,
            identity=FileIdentity.from_stat(anchor_stat),
            supplied_path=cwd,
            physical_path=physical_path,
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    except BaseException:
        os.close(fd)
        raise


def open_capture_root(anchor: ProjectAnchor, *, create: bool) -> CaptureRoot:
    """Open the capture-root chain relative to ``anchor`` without following symlinks."""

    opened: list[int] = []
    try:
        autoskillit_fd = _open_directory_component(
            anchor.fd, CAPTURE_PATH_COMPONENTS[0], create=create
        )
        opened.append(autoskillit_fd)
        temp_fd = _open_directory_component(
            autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], create=create
        )
        opened.append(temp_fd)
        capture_fd = _open_directory_component(temp_fd, CAPTURE_PATH_COMPONENTS[2], create=create)
        opened.append(capture_fd)
        return CaptureRoot(
            autoskillit_fd=autoskillit_fd,
            temp_fd=temp_fd,
            fd=capture_fd,
            autoskillit_identity=_identity(autoskillit_fd),
            temp_identity=_identity(temp_fd),
            identity=_identity(capture_fd),
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    except BaseException:
        for fd in reversed(opened):
            os.close(fd)
        raise


@contextmanager
def open_capture_lifecycle(
    requested_cwd: str,
    *,
    create: bool = False,
) -> Iterator[CaptureLifecycleStore]:
    """Open a lifecycle store from a validated payload cwd."""

    anchor = open_project_anchor(requested_cwd)
    root: CaptureRoot | None = None
    try:
        root = open_capture_root(anchor, create=create)
        yield CaptureLifecycleStore.from_open_authorities(anchor, root)
    finally:
        try:
            if root is not None:
                root.close()
        finally:
            anchor.close()


def create_capture_artifact(
    root: CaptureRoot,
    capture_id: str,
    lifecycle: CaptureLifecycleStore,
) -> CaptureArtifact:
    """Stage and publish a managed artifact beneath an open capture root."""

    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise CaptureSetupError("invalid capture id")
    try:
        fd, lease_fd, public_name, raw_identity = lifecycle.create_artifact(capture_id)
        identity = FileIdentity(device=raw_identity[0], inode=raw_identity[1])
        return CaptureArtifact(
            fd=fd,
            name=public_name,
            identity=identity,
            lease_fd=lease_fd,
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    except (CaptureLifecycleError, OSError) as exc:
        raise CaptureSetupError("cannot create managed capture artifact") from exc


def _duplicate_artifact_writer(artifact: CaptureArtifact) -> int:
    """Duplicate the artifact fd for the drain stage without transferring it to Bash."""

    writer_fd = -1
    try:
        writer_fd = os.dup(artifact.fd)
        if not _same_identity(writer_fd, artifact.identity):
            raise CaptureSetupError("duplicated capture artifact identity changed")
        return writer_fd
    except (CaptureSetupError, OSError) as exc:
        if writer_fd >= 0:
            try:
                os.close(writer_fd)
            except OSError:
                pass
        if isinstance(exc, CaptureSetupError):
            raise
        raise CaptureSetupError("cannot duplicate capture artifact fd") from exc


def _read_bounded_file_at(directory_fd: int, name: str) -> dict:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_mode & stat.S_IWOTH:
            return {}
        data = bytearray()
        while len(data) <= _MAX_POLICY_FILE_BYTES:
            chunk = os.read(fd, min(8192, _MAX_POLICY_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_POLICY_FILE_BYTES:
            return {}
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}
    finally:
        os.close(fd)


def _policy_inline_bytes(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return _DEFAULT_INLINE_BYTES
    return min(value, _MAX_INLINE_BYTES)


def read_capture_policy(anchor: ProjectAnchor) -> CapturePolicy:
    """Read output policy only through verified project/temp directory descriptors."""

    autoskillit_fd = -1
    temp_fd = -1
    try:
        try:
            autoskillit_fd = _open_directory_component(
                anchor.fd, CAPTURE_PATH_COMPONENTS[0], create=False
            )
            temp_fd = _open_directory_component(
                autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], create=False
            )
        except CaptureSetupError:
            return CapturePolicy()
        base = _read_bounded_file_at(temp_fd, HOOK_CONFIG_FILENAME)
        overlay = _read_bounded_file_at(temp_fd, HOOK_CONFIG_OVERLAY_FILENAME)
        merged = merge_hook_configs(base, overlay)
        section = merged.get("output_budget_policy", {})
        if not isinstance(section, dict):
            section = {}
        return CapturePolicy(
            disabled=section.get("disabled") is True,
            inline_bytes=_policy_inline_bytes(section.get("shell_max_inline_bytes")),
        )
    finally:
        try:
            if temp_fd >= 0:
                os.close(temp_fd)
        finally:
            if autoskillit_fd >= 0:
                os.close(autoskillit_fd)


def _open_and_match_directory(parent_fd: int, name: str, expected: FileIdentity) -> int:
    try:
        fd = _open_directory_component(parent_fd, name, create=False)
    except CaptureSetupError:
        return -1
    try:
        matches = _same_identity(fd, expected)
    except BaseException:
        os.close(fd)
        raise
    if not matches:
        os.close(fd)
        return -1
    return fd


def current_artifact_path_if_bound(
    anchor: ProjectAnchor,
    root: CaptureRoot,
    artifact: CaptureArtifact,
) -> str | None:
    """Return a path only if the current pathname chain still binds to all opened fds."""

    opened: list[int] = []
    try:
        try:
            marker_physical_path = Path(os.path.realpath(anchor.supplied_path))
            project_fd = os.open(
                marker_physical_path,
                _DIRECTORY_FLAGS,
            )
        except OSError:
            return None
        opened.append(project_fd)
        if not _same_identity(project_fd, anchor.identity):
            return None

        autoskillit_fd = _open_and_match_directory(
            project_fd, CAPTURE_PATH_COMPONENTS[0], root.autoskillit_identity
        )
        if autoskillit_fd < 0:
            return None
        opened.append(autoskillit_fd)

        temp_fd = _open_and_match_directory(
            autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], root.temp_identity
        )
        if temp_fd < 0:
            return None
        opened.append(temp_fd)

        capture_fd = _open_and_match_directory(temp_fd, CAPTURE_PATH_COMPONENTS[2], root.identity)
        if capture_fd < 0:
            return None
        opened.append(capture_fd)

        try:
            current_artifact_fd = os.open(artifact.name, _READ_FLAGS, dir_fd=capture_fd)
        except OSError:
            return None
        opened.append(current_artifact_fd)
        current_value = os.fstat(current_artifact_fd)
        if (
            FileIdentity.from_stat(current_value) != artifact.identity
            or not stat.S_ISREG(current_value.st_mode)
            or current_value.st_nlink != 1
            or current_value.st_mode & stat.S_IWOTH
        ):
            return None
        return str(marker_physical_path.joinpath(*CAPTURE_PATH_COMPONENTS, artifact.name))
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _wrap_user_command(command: str) -> str:
    separator = "" if command.endswith("\n") else "\n"
    return f"(\ntrap '__as_user_ec=$?; wait; exit \"$__as_user_ec\"' EXIT\n{command}{separator})"


def _spawn_bash(
    anchor: ProjectAnchor,
    bash_path: str,
    command: str,
    *,
    capture_output: bool,
) -> subprocess.Popen[bytes]:
    try:
        original_cwd_fd = os.open(".", _DIRECTORY_FLAGS & ~getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CaptureSetupError("cannot preserve runner cwd") from exc

    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        os.fchdir(anchor.fd)
        process = subprocess.Popen(
            [bash_path, "-c", _wrap_user_command(command)],
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
            close_fds=True,
        )
    except OSError as exc:
        raise CaptureSetupError("cannot spawn capture shell") from exc
    finally:
        try:
            os.fchdir(original_cwd_fd)
        except OSError as exc:
            restore_error = exc
        os.close(original_cwd_fd)

    if restore_error is not None:
        if process is not None:
            _settle_failed_capture(process)
        raise CaptureSetupError("cannot restore runner cwd") from restore_error
    if process is None:
        raise CaptureSetupError("capture shell did not start")
    return process


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "capture artifact write made no progress")
        view = view[written:]


def _drain_capture(
    process: subprocess.Popen[bytes],
    artifact_writer_fd: int,
    inline_bytes: int,
) -> _DrainResult:
    """Read the combined subprocess pipe and persist bounded replay metadata."""

    stream = process.stdout
    if stream is None:
        raise CaptureSetupError("capture pipe unavailable")

    head_limit = (2 * inline_bytes) // 3
    tail_limit = inline_bytes - head_limit
    total = 0
    digest = hashlib.sha256()
    inline = bytearray()
    head = bytearray()
    tail = bytearray()
    write_error: OSError | None = None

    while True:
        chunk = stream.read(_DRAIN_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
        if write_error is None:
            try:
                _write_all(artifact_writer_fd, chunk)
            except OSError as exc:
                write_error = exc
        if len(inline) <= inline_bytes:
            remaining = inline_bytes + 1 - len(inline)
            inline.extend(chunk[:remaining])
        if len(head) < head_limit:
            head.extend(chunk[: head_limit - len(head)])
        if tail_limit:
            tail.extend(chunk)
            if len(tail) > tail_limit:
                del tail[:-tail_limit]

    return _DrainResult(
        total_bytes=total,
        sha256=digest.hexdigest(),
        inline=bytes(inline),
        head=bytes(head),
        tail=bytes(tail),
        write_error=write_error,
    )


def _verify_capture_artifact(artifact: CaptureArtifact, result: _DrainResult) -> None:
    """Verify persisted bytes through the retained artifact descriptor."""

    value = os.fstat(artifact.fd)
    if (
        FileIdentity.from_stat(value) != artifact.identity
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_size != result.total_bytes
    ):
        raise CaptureSetupError("capture artifact metadata changed")

    digest = hashlib.sha256()
    offset = 0
    while offset < result.total_bytes:
        chunk = os.pread(
            artifact.fd,
            min(_DRAIN_CHUNK_BYTES, result.total_bytes - offset),
            offset,
        )
        if not chunk:
            raise CaptureSetupError("capture artifact readback ended early")
        digest.update(chunk)
        offset += len(chunk)
    if digest.hexdigest() != result.sha256:
        raise CaptureSetupError("capture artifact content changed")


def _normalized_returncode(returncode: int) -> int:
    return 128 + (-returncode) if returncode < 0 else returncode


def _capture_event(reason_code: str, decision: str) -> PolicyEvent:
    return PolicyEvent(
        hook_id="shell_capture_hook",
        hook_version=1,
        event="PreToolUse",
        decision=decision,
        reason_code=reason_code,
    )


def _emit_failure(detail: str) -> None:
    prefix = render_capture_marker(_capture_event("CAPTURE_FAILED", "deny"))
    safe_detail = " ".join(detail.split()).replace("]", "\\u005d")[:240]
    sys.stderr.write(f"{prefix} {safe_detail}]\n")


def _capture_failure_return(detail: str, returncode: int | None) -> int:
    try:
        _emit_failure(detail)
    except _CAPTURE_RUNTIME_ERRORS:
        return _CAPTURE_FAILURE_RETURN_CODE
    if returncode is None or returncode == 0:
        return _CAPTURE_FAILURE_RETURN_CODE
    return returncode


def _encode_marker_path(path: str) -> str:
    """Return a single-line path that cannot terminate the provenance marker."""

    return json.dumps(path, ensure_ascii=True)[1:-1].replace("]", "\\u005d")


def _emit_capture(
    result: _DrainResult,
    artifact_path: str | None,
    inline_bytes: int,
) -> None:
    if result.total_bytes <= inline_bytes:
        payload = result.inline
    else:
        prefix = render_capture_marker(_capture_event("SHELL_OUTPUT_CAPTURED", "input rewrite"))
        path = _encode_marker_path(artifact_path) if artifact_path is not None else "unavailable"
        marker = (
            f"\n{prefix} full output {result.total_bytes} bytes -> {path} "
            f"sha256={result.sha256} complete=true]\n"
        ).encode()
        payload = result.head + marker + result.tail
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _resolve_bash() -> str:
    for candidate in _TRUSTED_BASH_CANDIDATES:
        if not os.path.isabs(candidate):
            continue
        try:
            fd = os.open(candidate, _READ_FLAGS)
        except OSError:
            continue
        try:
            value = os.fstat(fd)
            if (
                stat.S_ISREG(value.st_mode)
                and value.st_uid == 0
                and value.st_mode & _EXECUTABLE_MODE_BITS
                and not value.st_mode & _UNTRUSTED_WRITE_BITS
            ):
                return candidate
        except OSError:
            pass
        finally:
            os.close(fd)
    raise CaptureSetupError("trusted bash executable unavailable")


def _settle_failed_capture(process: subprocess.Popen[bytes]) -> int | None:
    if process.stdout is not None:
        try:
            process.stdout.close()
        except _CAPTURE_RUNTIME_ERRORS:
            pass
    try:
        running = process.poll() is None
    except _CAPTURE_RUNTIME_ERRORS:
        running = True
    if running:
        try:
            process.terminate()
        except _CAPTURE_RUNTIME_ERRORS:
            try:
                process.kill()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
    try:
        return _normalized_returncode(process.wait(timeout=_PROCESS_SETTLE_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except _CAPTURE_RUNTIME_ERRORS:
            pass
        try:
            return _normalized_returncode(process.wait(timeout=_PROCESS_SETTLE_TIMEOUT_SECONDS))
        except _CAPTURE_RUNTIME_ERRORS:
            return None
    except _CAPTURE_RUNTIME_ERRORS:
        return None


def run_capture(command: str, cwd: str, capture_id: str) -> int:
    """Run ``command`` from a descriptor-anchored project and capture its output."""

    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureSetupError("invalid command encoding") from exc
    if (
        not _CAPTURE_ID_RE.fullmatch(capture_id)
        or "\x00" in command
        or len(command_bytes) > _MAX_COMMAND_BYTES
    ):
        raise CaptureSetupError("invalid capture request")

    anchor = open_project_anchor(cwd)
    root: CaptureRoot | None = None
    lifecycle: CaptureLifecycleStore | None = None
    artifact: CaptureArtifact | None = None
    artifact_writer_fd = -1
    process: subprocess.Popen[bytes] | None = None
    try:
        policy = read_capture_policy(anchor)
        bash_path = _resolve_bash()
        if policy.disabled:
            process = _spawn_bash(anchor, bash_path, command, capture_output=False)
            return _normalized_returncode(process.wait())

        root = open_capture_root(anchor, create=True)
        lifecycle = CaptureLifecycleStore.from_open_authorities(anchor, root)
        artifact = create_capture_artifact(root, capture_id, lifecycle)
        artifact_writer_fd = _duplicate_artifact_writer(artifact)
        returncode: int | None = None
        result: _DrainResult | None = None
        terminal_committed = False
        failure_stage = "capture process spawn"
        try:
            process = _spawn_bash(anchor, bash_path, command, capture_output=True)
            failure_stage = "capture readback"
            result = _drain_capture(process, artifact_writer_fd, policy.inline_bytes)
            failure_stage = "capture process wait"
            returncode = _normalized_returncode(process.wait())
            if result.write_error is not None:
                failure_stage = "capture failed-state commit"
                lifecycle.finalize_capture(
                    capture_id,
                    size=result.total_bytes,
                    sha256=result.sha256,
                    failed=True,
                )
                terminal_committed = True
                return _capture_failure_return("capture artifact write failed", returncode)
            failure_stage = "capture artifact integrity verification"
            _verify_capture_artifact(artifact, result)
            failure_stage = "capture finalization"
            lifecycle.finalize_capture(
                capture_id,
                size=result.total_bytes,
                sha256=result.sha256,
                failed=False,
            )
            terminal_committed = True
            failure_stage = "capture marker verification"
            artifact_path = current_artifact_path_if_bound(anchor, root, artifact)
            failure_stage = "capture replay emission"
            _emit_capture(result, artifact_path, policy.inline_bytes)
            return returncode
        except _CAPTURE_RUNTIME_ERRORS as exc:
            if returncode is None and process is not None:
                returncode = _settle_failed_capture(process)
            if not terminal_committed:
                try:
                    lifecycle.finalize_capture(
                        capture_id,
                        size=(
                            result.total_bytes
                            if result is not None
                            else max(0, os.fstat(artifact.fd).st_size)
                        ),
                        sha256=result.sha256 if result is not None else "",
                        failed=True,
                    )
                    terminal_committed = True
                except _CAPTURE_RUNTIME_ERRORS:
                    failure_stage = "capture failed-state commit"
            return _capture_failure_return(
                f"{failure_stage} failed: {type(exc).__name__}: {exc}",
                returncode,
            )
    finally:
        if process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if artifact_writer_fd >= 0:
            try:
                os.close(artifact_writer_fd)
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if artifact is not None:
            try:
                artifact.close()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if root is not None:
            try:
                root.close()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        try:
            anchor.close()
        except _CAPTURE_RUNTIME_ERRORS:
            pass
        if artifact is not None:
            try:
                artifact.release_lease()
            except _CAPTURE_RUNTIME_ERRORS:
                pass


def _decode_command(value: str) -> str:
    if len(value) > _MAX_ENCODED_COMMAND_BYTES:
        raise CaptureSetupError("encoded command exceeds limit")
    try:
        raw = base64.b64decode(value, validate=True)
        command = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CaptureSetupError("invalid command transport") from exc
    if len(raw) > _MAX_COMMAND_BYTES or "\x00" in command:
        raise CaptureSetupError("decoded command exceeds limit")
    return command


def _dispatch_runner(
    verb: str,
    payload: str,
    requested_cwd: str,
    capture_id: str,
) -> int:
    if verb == "reject":
        _emit_failure("capture request rejected before command execution")
        return 1
    try:
        command = _decode_command(payload)
        return run_capture(command, requested_cwd, capture_id)
    except CaptureSetupError as exc:
        _emit_failure(str(exc))
        return 1
    except (OSError, subprocess.SubprocessError):
        _emit_failure("capture runner failed")
        return 1


def _emit_cleanup_failure(detail: str) -> None:
    safe_detail = " ".join(detail.split()).replace("]", "\\u005d")[:240]
    try:
        sys.stderr.write(f"[AutoSkillit shell capture cleanup failed: {safe_detail}]\n")
    except _CAPTURE_RUNTIME_ERRORS:
        pass


def _sweep_after_runner(requested_cwd: str) -> None:
    try:
        with open_capture_lifecycle(requested_cwd, create=False) as lifecycle:
            lifecycle.sweep()
    except CaptureSetupError:
        return
    except (CaptureLifecycleError, OSError) as exc:
        _emit_cleanup_failure(f"{type(exc).__name__}: {exc}")


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        _emit_failure("invalid capture runner invocation")
        return 1
    verb, payload, requested_cwd, capture_id = args
    if (
        verb not in {"run", "reject"}
        or (verb == "reject" and payload)
        or not isinstance(requested_cwd, str)
        or not requested_cwd
        or not os.path.isabs(requested_cwd)
        or "\x00" in requested_cwd
    ):
        _emit_failure("invalid capture runner invocation")
        return 1
    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        _emit_failure("invalid capture id")
        return 1
    try:
        user_result = _dispatch_runner(verb, payload, requested_cwd, capture_id)
    except _CAPTURE_RUNTIME_ERRORS:
        user_result = _capture_failure_return("capture runner failed", None)
    _sweep_after_runner(requested_cwd)
    return user_result


if __name__ == "__main__":
    sys.exit(_main())
