"""One-shot request correlation for Claude exploration tool calls.

This module is stdlib-only because the producer also runs as a standalone hook.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REQUEST_DIRECTORY = "exploration-requests"
_RECORD_PREFIX = "exploration-request-"
_RECORD_SUFFIX = ".json"
_CLAIM_PREFIX = "exploration-request-claim-"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MAX_RECORD_BYTES = 512
_REQUEST_TTL_SECONDS = 30.0
_MAX_CLEANUP_ENTRIES = 256
SUPPORTED_EXPLORATION_REQUEST_TOOLS = frozenset(
    {
        "enable_exploration",
        "submit_exploration_query",
        "get_exploration_page",
        "resume_exploration_context",
    }
)
_clock: Callable[[], float] = time.time

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _valid_tool_name(tool_name: object) -> bool:
    return isinstance(tool_name, str) and tool_name in SUPPORTED_EXPLORATION_REQUEST_TOOLS


def _valid_session_id(session_id: object) -> bool:
    return isinstance(session_id, str) and 0 < len(session_id) <= 128


def _record_name(token: str) -> str:
    return f"{_RECORD_PREFIX}{token}{_RECORD_SUFFIX}"


def _open_child_directory(parent_fd: int, component: str, *, create: bool = False) -> int:
    created = False
    if create:
        try:
            os.mkdir(component, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
    if created:
        os.chmod(component, 0o700, dir_fd=parent_fd, follow_symlinks=False)
    return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def _open_request_directory(project_root: Path) -> int:
    root_fd = os.open(os.fspath(project_root), _DIRECTORY_FLAGS)
    opened = [root_fd]
    try:
        for component in (".autoskillit", "temp"):
            opened.append(_open_child_directory(opened[-1], component))
        request_fd = _open_child_directory(opened[-1], _REQUEST_DIRECTORY, create=True)
    except Exception:
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    for fd in reversed(opened):
        os.close(fd)
    return request_fd


def _read_bounded(fd: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = _MAX_RECORD_BYTES + 1
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    return payload if 0 < len(payload) <= _MAX_RECORD_BYTES else None


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("request-record write made no progress")
        view = view[written:]


def _cleanup_expired(request_fd: int, now: float) -> None:
    try:
        names = os.listdir(request_fd)
    except OSError:
        return
    if len(names) > _MAX_CLEANUP_ENTRIES:
        start = secrets.randbelow(len(names))
        names = names[start:] + names[:start]
    for name in names[:_MAX_CLEANUP_ENTRIES]:
        if not (
            name.startswith(_RECORD_PREFIX) or name.startswith(_CLAIM_PREFIX)
        ) or not name.endswith(_RECORD_SUFFIX):
            continue
        try:
            fd = os.open(name, _READ_FLAGS, dir_fd=request_fd)
        except OSError:
            continue
        try:
            metadata = os.fstat(fd)
            expired = now - metadata.st_mtime > _REQUEST_TTL_SECONDS
        finally:
            os.close(fd)
        if expired:
            try:
                os.unlink(name, dir_fd=request_fd)
            except OSError:
                pass


def _parse_record(payload: bytes, *, expected_tool_name: str, now: float) -> str | None:
    try:
        value: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {
        "session_id",
        "tool_name",
        "created_at",
    }:
        return None
    session_id = value["session_id"]
    tool_name = value["tool_name"]
    created_at = value["created_at"]
    if not _valid_session_id(session_id) or tool_name != expected_tool_name:
        return None
    if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
        return None
    age = now - float(created_at)
    if age < 0 or age > _REQUEST_TTL_SECONDS:
        return None
    return session_id


def write_exploration_request_record(
    project_root: str | os.PathLike[str],
    tool_name: str,
    session_id: str,
) -> str:
    """Write one bounded request record and return its opaque one-shot token."""
    if not _valid_tool_name(tool_name):
        raise ValueError("unsupported exploration tool name")
    if not _valid_session_id(session_id):
        raise ValueError("native session ID must contain 1 to 128 characters")

    now = _clock()
    request_fd = _open_request_directory(Path(project_root))
    try:
        _cleanup_expired(request_fd, now)
        token = secrets.token_urlsafe(32)
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise RuntimeError("token_urlsafe returned an unexpected token shape")
        payload = json.dumps(
            {"session_id": session_id, "tool_name": tool_name, "created_at": now},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_RECORD_BYTES:
            raise ValueError("exploration request record exceeds its size bound")
        fd = os.open(_record_name(token), _WRITE_FLAGS, 0o600, dir_fd=request_fd)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, payload)
        finally:
            os.close(fd)
        return token
    finally:
        os.close(request_fd)


def consume_exploration_request_record(
    project_root: str | os.PathLike[str],
    expected_tool_name: str,
    token: str,
) -> str | None:
    """Atomically consume one tool-bound request record, returning its session ID."""
    if not _valid_tool_name(expected_tool_name):
        return None
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        return None

    try:
        request_fd = _open_request_directory(Path(project_root))
    except OSError:
        return None
    claim_name = f"{_CLAIM_PREFIX}{token}-{os.getpid()}-{secrets.token_hex(8)}{_RECORD_SUFFIX}"
    try:
        try:
            os.rename(
                _record_name(token),
                claim_name,
                src_dir_fd=request_fd,
                dst_dir_fd=request_fd,
            )
        except OSError:
            return None
        try:
            try:
                fd = os.open(claim_name, _READ_FLAGS, dir_fd=request_fd)
            except OSError:
                return None
            try:
                metadata = os.fstat(fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or not 0 < metadata.st_size <= _MAX_RECORD_BYTES
                ):
                    return None
                payload = _read_bounded(fd)
            finally:
                os.close(fd)
            if payload is None:
                return None
            return _parse_record(
                payload,
                expected_tool_name=expected_tool_name,
                now=_clock(),
            )
        finally:
            try:
                os.unlink(claim_name, dir_fd=request_fd)
            except OSError:
                pass
    finally:
        os.close(request_fd)
