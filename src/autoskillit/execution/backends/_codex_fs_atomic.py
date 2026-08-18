"""Stateless filesystem primitives used by the Codex session storage layer.

These helpers are pure with respect to module-level state — they only wrap
the stdlib (`os`, `pathlib`, `subprocess`) and the parsed `_codex_parse`
helpers. The transaction-boundary core (`CodexSessionStore`,
`CodexInteractiveSessionLease`, `_FileLease`) remains in
`_codex_session_storage.py`.
"""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import time
from pathlib import Path

import regex as re

from autoskillit.core import get_logger

logger = get_logger(__name__)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _require_real_directory(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing {label}: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RuntimeError(f"{label} must be a non-symlink directory: {path}")


def _fsync_directory(path: Path) -> None:
    _require_real_directory(path, label="fsync directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_real_directory(path.parent, label="JSON parent")
    if _lexists(path) and path.is_symlink():
        raise RuntimeError(f"Refusing to replace symlink JSON destination: {path}")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _write_reconciliation_audit(path: Path, payload: object) -> None:
    """Publish one immutable, crash-safe reconciliation authorization."""
    _require_real_directory(path.parent, label="reconciliation audit root")
    if _lexists(path):
        raise FileExistsError(f"Reconciliation audit already exists: {path.name}")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)
    try:
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        finally:
            _fsync_directory(path.parent)


def _read_bounded(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} is not a regular file")
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise ValueError(f"{path.name} exceeds the {limit}-byte bound")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _ensure_directory_chain(root: Path, relative: Path) -> Path:
    _require_real_directory(root, label="storage root")
    cursor = root
    for part in relative.parts:
        if part in {"", ".", ".."} or "/" in part or "\\" in part:
            raise RuntimeError(f"Unsafe storage directory component: {part!r}")
        next_path = cursor / part
        created = False
        try:
            next_path.mkdir()
            created = True
        except FileExistsError:
            pass
        _require_real_directory(next_path, label="storage directory")
        if created:
            _fsync_directory(cursor)
        cursor = next_path
    return cursor


def _decode_mount_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _filesystem_mount_root(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    device = resolved.stat().st_dev
    mount_root = resolved
    while mount_root.parent != mount_root:
        parent = mount_root.parent
        if parent.stat().st_dev != device:
            break
        mount_root = parent
    return mount_root


def _filesystem_type(path: Path) -> str:
    if sys.platform == "darwin":
        try:
            mount_root = _filesystem_mount_root(path)
            result = subprocess.run(
                ("/usr/sbin/diskutil", "info", "-plist", str(mount_root)),
                capture_output=True,
                check=False,
                timeout=5,
            )
            if result.returncode != 0:
                raise RuntimeError("Unable to classify the Codex storage filesystem with diskutil")
            payload = plistlib.loads(result.stdout)
            filesystem_type = payload.get("FilesystemType")
            if not isinstance(filesystem_type, str) or not filesystem_type:
                raise RuntimeError("diskutil did not report a filesystem type")
            return filesystem_type.lower()
        except (OSError, plistlib.InvalidFileException) as exc:
            raise RuntimeError("Unable to classify the Codex storage filesystem") from exc
    if not sys.platform.startswith("linux"):
        raise RuntimeError(f"Codex durable views cannot classify filesystems on {sys.platform}")
    try:
        raw = _read_bounded(Path("/proc/self/mountinfo"), 4 * 1024 * 1024)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Unable to classify the Codex storage filesystem") from exc
    resolved = path.resolve(strict=True)
    selected: tuple[int, str] | None = None
    for raw_line in raw.decode("utf-8", errors="strict").splitlines():
        before, separator, after = raw_line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        trailing = after.split()
        if len(fields) < 5 or not trailing:
            continue
        mount_path = Path(_decode_mount_path(fields[4]))
        if resolved == mount_path or resolved.is_relative_to(mount_path):
            length = len(mount_path.parts)
            if selected is None or length > selected[0]:
                selected = (length, trailing[0])
    if selected is None:
        raise RuntimeError(f"Unable to classify Codex storage mount: {resolved}")
    return selected[1]


def _replace_symlink(path: Path, target: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.link")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    temporary.symlink_to(target)
    os.replace(temporary, path)
    _fsync_directory(path.parent)
