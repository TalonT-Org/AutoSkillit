"""Private durable storage for interactive Codex rollout views."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zstandard

from autoskillit.core import (
    CODEX_ACTIVE_VIEWS_SUBDIR,
    CODEX_ARCHIVED_SESSIONS_SUBDIR,
    CODEX_SESSIONS_SUBDIR,
    BareResume,
    CookSessionHandle,
    NamedResume,
    NoResume,
    ResumeSpec,
    SessionSummary,
    default_log_dir,
)

_VIEW_ID_RE = re.compile(r"^[0-9a-f]{16}-[1-9][0-9]*$")
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ROLLOUT_SUFFIXES = (".jsonl", ".jsonl.zst")
_INDEX_READ_LIMIT = 4 * 1024 * 1024
_ROLLOUT_METADATA_LIMIT = 64 * 1024
_MANIFEST_READ_LIMIT = 256 * 1024
_MANIFEST_NAME = "manifest.json"
_LOCKS_SUBDIR = ".locks"
_INDEX_NAME = "codex-session-index.json"
_MANIFEST_STATES = frozenset({"prepared", "running", "finalizing", "complete", "failed"})
_STORE_TO_PUBLIC = {"active": "sessions", "archived": "archived_sessions"}
_PUBLIC_TO_STORE = {value: key for key, value in _STORE_TO_PUBLIC.items()}
_SUPPORTED_LOCAL_FILESYSTEMS = frozenset(
    {
        "apfs",
        "bcachefs",
        "btrfs",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "hfs",
        "hfsplus",
        "overlay",
        "tmpfs",
        "xfs",
        "zfs",
    }
)
_INERT_NAMES = {
    "sessions": ".inert-sessions",
    "archived_sessions": ".inert-archived_sessions",
}


def codex_session_index_path() -> Path:
    """Return the one production path for the derived Codex cook index."""
    return default_log_dir() / _INDEX_NAME


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


def _read_prefix(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if path.name.endswith(".zst"):
            with os.fdopen(fd, "rb", closefd=False) as source:
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    return reader.read(limit)
        return os.read(fd, limit)
    finally:
        os.close(fd)


def _safe_relative_value(value: str) -> Path:
    if not value or "\\" in value:
        raise RuntimeError(f"Unsafe relative rollout path: {value!r}")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"Unsafe relative rollout path: {value!r}")
    if not relative.name.endswith(_ROLLOUT_SUFFIXES):
        raise RuntimeError(f"Unsupported rollout filename: {value!r}")
    return relative


def _safe_relative(path: Path, root: Path) -> Path:
    _require_real_directory(root, label="rollout root")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing rollout file: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeError(f"Rollout must be a regular non-symlink file: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Rollout escapes its root: {path}") from exc
    relative = _safe_relative_value(relative.as_posix())
    cursor = root
    for part in relative.parts[:-1]:
        cursor /= part
        _require_real_directory(cursor, label="rollout parent")
    return relative


def _rollout_files(root: Path) -> Iterator[Path]:
    if not _lexists(root):
        return
    _require_real_directory(root, label="rollout root")
    found: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directory_names:
            candidate = parent / name
            if candidate.is_symlink():
                raise RuntimeError(f"Symlink directory in rollout tree: {candidate}")
        for name in file_names:
            candidate = parent / name
            if candidate.is_symlink():
                raise RuntimeError(f"Symlink file in rollout tree: {candidate}")
            if name.endswith(_ROLLOUT_SUFFIXES):
                _safe_relative(candidate, root)
                found.append(candidate)
    yield from sorted(found)


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


def _filesystem_type(path: Path) -> str:
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


def _thread_id_from_bytes(data: bytes) -> str | None:
    for raw_line in data.splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(row, Mapping):
            continue
        if row.get("type") == "thread.started" and isinstance(row.get("thread_id"), str):
            return str(row["thread_id"])
        if row.get("type") == "session_meta":
            payload = row.get("payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("id"), str):
                return str(payload["id"])
    return None


def _thread_id(path: Path) -> str | None:
    try:
        return _thread_id_from_bytes(_read_prefix(path, _ROLLOUT_METADATA_LIMIT))
    except (OSError, ValueError, zstandard.ZstdError):
        return None


def _identity(path: Path) -> tuple[int, int]:
    file_stat = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"Expected regular rollout file: {path}")
    return file_stat.st_dev, file_stat.st_ino


def _replace_symlink(path: Path, target: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.link")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    temporary.symlink_to(target)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


@dataclass(slots=True)
class _FileLease:
    path: Path
    fd: int = field(init=False)

    @classmethod
    def acquire(cls, path: Path, *, nonblocking: bool = False) -> _FileLease:
        path.parent.mkdir(parents=True, exist_ok=True)
        _require_real_directory(path.parent, label="lock directory")
        if _lexists(path) and path.is_symlink():
            raise RuntimeError(f"Refusing symlink lock file: {path}")
        instance = cls(path=path)
        instance.fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(instance.fd, operation)
        except BaseException:
            os.close(instance.fd)
            raise
        owner = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_ns": time.time_ns(),
        }
        os.ftruncate(instance.fd, 0)
        os.write(instance.fd, json.dumps(owner, sort_keys=True).encode())
        os.fsync(instance.fd)
        return instance

    def release(self) -> None:
        if self.fd < 0:
            return
        fd, self.fd = self.fd, -1
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@dataclass(slots=True)
class CodexInteractiveSessionLease(AbstractContextManager[CookSessionHandle]):
    """One owned attempt view, including durable spawn/reap proof."""

    store: CodexSessionStore
    session_home: Path
    launch_id: str
    attempt: int
    current_resume_spec: ResumeSpec
    view_id: str
    view_path: Path
    manifest: dict[str, Any]
    view_lease: _FileLease
    inert_targets: dict[str, Path]
    thread_lease: _FileLease | None = None
    _entered: bool = False
    _closed: bool = False

    def __enter__(self) -> CookSessionHandle:
        if self._entered:
            raise RuntimeError("Codex attempt lease is not reentrant")
        try:
            self.store._enter_attempt(self)
        except BaseException as entry_error:
            self._closed = True
            failures: list[BaseException] = [entry_error]
            try:
                self.store._abort_pre_spawn(self)
            except BaseException as cleanup_error:
                failures.append(cleanup_error)
            self._release_leases(failures)
            if len(failures) == 1:
                raise
            raise BaseExceptionGroup("Codex attempt entry failed", failures)
        self._entered = True
        pass_fds = [self.view_lease.fd]
        if self.thread_lease is not None:
            pass_fds.append(self.thread_lease.fd)
        return CookSessionHandle(
            view_id=self.view_id,
            pass_fds=tuple(fd for fd in pass_fds if fd >= 0),
            _record_spawn=self._record_spawn,
            _record_reaped=self._record_reaped,
        )

    def _record_spawn(self, pid: int, pgid: int) -> None:
        if not self._entered or self._closed:
            raise RuntimeError("Cannot record spawn outside an active Codex attempt")
        if self.manifest.get("child_pid") is not None:
            raise RuntimeError("Codex attempt spawn was already recorded")
        if pid <= 0 or pgid <= 0:
            raise ValueError("Child pid and pgid must be positive")
        self.manifest.update(
            state="running",
            child_pid=pid,
            child_pgid=pgid,
            reaped=False,
        )
        self.store._write_manifest(self)

    def _record_reaped(self, pid: int, pgid: int) -> None:
        if not self._entered or self._closed:
            raise RuntimeError("Cannot record reap outside an active Codex attempt")
        if self.manifest.get("child_pid") != pid or self.manifest.get("child_pgid") != pgid:
            raise RuntimeError("Reaped child identity does not match the recorded spawn")
        if self.manifest.get("reaped") is True:
            raise RuntimeError("Codex attempt reap was already recorded")
        self.manifest["reaped"] = True
        self.manifest["reaped_ns"] = time.time_ns()
        self.store._write_manifest(self)

    def _release_leases(self, failures: list[BaseException]) -> None:
        if self.thread_lease is not None:
            try:
                self.thread_lease.release()
            except BaseException as release_error:
                failures.append(release_error)
        try:
            self.view_lease.release()
        except BaseException as release_error:
            failures.append(release_error)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        if self._closed:
            return None
        self._closed = True
        failures: list[BaseException] = []
        try:
            self.store._exit_attempt(self)
        except BaseException as cleanup_error:
            failures.append(cleanup_error)
        finally:
            self._release_leases(failures)
        if exc is not None:
            if failures:
                raise BaseExceptionGroup(
                    "Codex attempt body and cleanup failed",
                    [exc, *failures],
                )
            return False
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("Codex attempt cleanup failed", failures)
        return None


class CodexSessionStore:
    """Canonical rollouts, attempt views, recovery, and derived listing index."""

    def __init__(self, log_dir: Path, index_path: Path | None = None) -> None:
        self.log_dir = Path(log_dir).expanduser().resolve(strict=False)
        self.active_root = self.log_dir / CODEX_SESSIONS_SUBDIR
        self.archive_root = self.log_dir / CODEX_ARCHIVED_SESSIONS_SUBDIR
        self.views_root = self.log_dir / CODEX_ACTIVE_VIEWS_SUBDIR
        self.locks_root = self.views_root / _LOCKS_SUBDIR
        self.index_path = (
            Path(index_path).expanduser().resolve(strict=False)
            if index_path is not None
            else self.log_dir / _INDEX_NAME
        )

    def _ensure_roots(self) -> None:
        for root in (self.active_root, self.archive_root, self.views_root, self.locks_root):
            root.mkdir(parents=True, exist_ok=True)
            _require_real_directory(root, label="Codex storage root")
        devices = {
            self.active_root.stat().st_dev,
            self.archive_root.stat().st_dev,
            self.views_root.stat().st_dev,
        }
        if len(devices) != 1:
            raise RuntimeError("Codex rollout stores and views must share one filesystem")
        filesystem_types = {
            _filesystem_type(self.active_root),
            _filesystem_type(self.archive_root),
            _filesystem_type(self.views_root),
        }
        if len(filesystem_types) != 1 or not filesystem_types <= _SUPPORTED_LOCAL_FILESYSTEMS:
            raise RuntimeError(
                "Codex durable views require one supported local filesystem; "
                f"found {sorted(filesystem_types)}"
            )

    def _thread_lock_path(self, thread_id: str) -> Path:
        if _THREAD_ID_RE.fullmatch(thread_id) is None:
            raise ValueError(f"Invalid Codex thread id: {thread_id!r}")
        key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
        return self.locks_root / f"thread-{key}.lock"

    def prepare_attempt(
        self,
        *,
        session_home: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> CodexInteractiveSessionLease:
        self._ensure_roots()
        view_id = f"{launch_id}-{attempt}"
        if _VIEW_ID_RE.fullmatch(view_id) is None:
            raise ValueError(f"Invalid Codex attempt identity: {view_id!r}")
        session_home = Path(session_home).resolve(strict=True)
        inert_targets = self._validate_inert_home(session_home)
        view_path = self.views_root / view_id
        if os.path.lexists(view_path):
            raise FileExistsError(f"Codex attempt view already exists: {view_id}")
        view_path.mkdir(mode=0o700)
        (view_path / "sessions").mkdir()
        (view_path / "archived_sessions").mkdir()
        _fsync_directory(view_path)
        _fsync_directory(self.views_root)
        view_lease = _FileLease.acquire(self.locks_root / f"view-{view_id}.lock")
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "launch_id": launch_id,
            "attempt": attempt,
            "view_id": view_id,
            "state": "prepared",
            "child_pid": None,
            "child_pgid": None,
            "reaped": False,
            "resume_thread_id": None,
            "resume_source_store": None,
            "resume_source_relpath": None,
            "final_store": None,
            "final_relpath": None,
        }
        thread_lease: _FileLease | None = None
        try:
            if isinstance(current_resume_spec, NamedResume):
                thread_id = current_resume_spec.session_id
                thread_lease = _FileLease.acquire(
                    self._thread_lock_path(thread_id),
                    nonblocking=True,
                )
                located = self._locate_with_store(thread_id)
                if located is None:
                    raise FileNotFoundError(f"Codex resume rollout not found: {thread_id}")
                source_store, source_path = located
                source_root = self.active_root if source_store == "active" else self.archive_root
                relative = _safe_relative(source_path, source_root)
                destination_root = view_path / _STORE_TO_PUBLIC[source_store]
                destination = destination_root / relative
                _ensure_directory_chain(destination_root, relative.parent)
                os.link(source_path, destination, follow_symlinks=False)
                if _identity(destination) != _identity(source_path):
                    raise RuntimeError("Codex resume hard link identity mismatch")
                _fsync_directory(destination.parent)
                manifest.update(
                    resume_thread_id=thread_id,
                    resume_source_store=source_store,
                    resume_source_relpath=relative.as_posix(),
                )
            elif isinstance(current_resume_spec, BareResume):
                raise RuntimeError("Bare resume must be resolved before attempt preparation")
            elif not isinstance(current_resume_spec, NoResume):
                raise TypeError("Unsupported Codex resume specification")
            lease = CodexInteractiveSessionLease(
                store=self,
                session_home=session_home,
                launch_id=launch_id,
                attempt=attempt,
                current_resume_spec=current_resume_spec,
                view_id=view_id,
                view_path=view_path,
                manifest=manifest,
                view_lease=view_lease,
                inert_targets=inert_targets,
                thread_lease=thread_lease,
            )
            self._write_manifest(lease)
            return lease
        except BaseException:
            if thread_lease is not None:
                thread_lease.release()
            view_lease.release()
            if _lexists(view_path):
                self._validate_pre_spawn_view(view_path, manifest, allow_missing_resume=True)
                shutil.rmtree(view_path)
                _fsync_directory(self.views_root)
            raise

    def _validate_inert_home(self, session_home: Path) -> dict[str, Path]:
        targets: dict[str, Path] = {}
        for public_name, inert_name in _INERT_NAMES.items():
            public_path = session_home / public_name
            inert_path = session_home / inert_name
            if not public_path.is_symlink():
                raise RuntimeError(f"{public_name} must be an inert symlink before view entry")
            resolved_public = public_path.resolve(strict=True)
            resolved_inert = inert_path.resolve(strict=True)
            if resolved_public != resolved_inert:
                raise RuntimeError(f"{public_name} does not resolve to its inert target")
            if not resolved_inert.is_dir() or resolved_inert.is_symlink():
                raise RuntimeError(f"Invalid inert rollout target: {resolved_inert}")
            if any(resolved_inert.iterdir()):
                raise RuntimeError(f"Inert rollout target is not empty: {resolved_inert}")
            if not resolved_inert.is_relative_to(session_home):
                raise RuntimeError("Inert rollout target escapes the generated home")
            targets[public_name] = resolved_inert
        return targets

    def _enter_attempt(self, lease: CodexInteractiveSessionLease) -> None:
        self._validate_inert_home(lease.session_home)
        for public_name in _INERT_NAMES:
            _replace_symlink(
                lease.session_home / public_name,
                lease.view_path / public_name,
            )

    def _restore_inert(self, lease: CodexInteractiveSessionLease) -> None:
        for public_name, target in lease.inert_targets.items():
            _replace_symlink(lease.session_home / public_name, target)
            if (lease.session_home / public_name).resolve(strict=True) != target:
                raise RuntimeError(f"Failed to restore inert {public_name} link")

    def _write_manifest(self, lease: CodexInteractiveSessionLease) -> None:
        _atomic_json(lease.view_path / _MANIFEST_NAME, lease.manifest)

    def _abort_pre_spawn(self, lease: CodexInteractiveSessionLease) -> None:
        lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
        try:
            self._restore_inert(lease)
            lease.manifest["state"] = "failed"
            self._write_manifest(lease)
            self._validate_pre_spawn_view(
                lease.view_path,
                lease.manifest,
                allow_missing_resume=True,
            )
            shutil.rmtree(lease.view_path)
            _fsync_directory(self.views_root)
        finally:
            lifecycle.release()

    def _exit_attempt(self, lease: CodexInteractiveSessionLease) -> None:
        if lease.manifest.get("child_pid") is None:
            self._abort_pre_spawn(lease)
            return
        self._restore_inert(lease)
        if lease.manifest.get("reaped") is not True:
            lease.manifest["state"] = "failed"
            self._write_manifest(lease)
            raise RuntimeError("Codex attempt lacks durable child-reaped proof")
        lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
        try:
            lease.manifest["state"] = "finalizing"
            self._write_manifest(lease)
            rows = self._promote_view(lease)
            if rows:
                self._merge_index_unlocked(rows)
            lease.manifest["state"] = "complete"
            self._write_manifest(lease)
            self._validate_completed_view(lease.view_path)
            shutil.rmtree(lease.view_path)
            _fsync_directory(self.views_root)
        finally:
            lifecycle.release()

    def _validate_pre_spawn_view(
        self,
        view_path: Path,
        manifest: Mapping[str, Any],
        *,
        allow_missing_resume: bool,
    ) -> None:
        _require_real_directory(view_path, label="Codex attempt view")
        expected_root_entries = {"sessions", "archived_sessions", _MANIFEST_NAME}
        actual_root_entries = {path.name for path in view_path.iterdir()}
        if not actual_root_entries <= expected_root_entries:
            raise RuntimeError("Never-running Codex view contains unexpected root entries")
        allowed: set[tuple[str, str]] = set()
        source_store = manifest.get("resume_source_store")
        source_relpath = manifest.get("resume_source_relpath")
        if isinstance(source_store, str) and isinstance(source_relpath, str):
            allowed.add((_STORE_TO_PUBLIC[source_store], source_relpath))
        found: set[tuple[str, str]] = set()
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            _require_real_directory(root, label="attempt rollout root")
            for directory, directory_names, file_names in os.walk(root, followlinks=False):
                parent = Path(directory)
                if any((parent / name).is_symlink() for name in directory_names):
                    raise RuntimeError("Never-running Codex view contains a symlink directory")
                for name in file_names:
                    path = parent / name
                    if path.is_symlink():
                        raise RuntimeError("Never-running Codex view contains a symlink file")
                    relative = _safe_relative(path, root).as_posix()
                    key = (public_name, relative)
                    if key not in allowed:
                        raise RuntimeError(
                            f"Never-running Codex view contains unexpected file: {path}"
                        )
                    found.add(key)
        if not allow_missing_resume and found != allowed:
            raise RuntimeError("Never-running Codex view is missing its resume hard link")
        for public_name, relative_value in found:
            store_name = _PUBLIC_TO_STORE[public_name]
            canonical_root = self.active_root if store_name == "active" else self.archive_root
            relative_path = _safe_relative_value(relative_value)
            canonical = canonical_root / relative_path
            staged = view_path / public_name / relative_path
            if not canonical.exists() or _identity(canonical) != _identity(staged):
                raise RuntimeError("Resume hard link lost its canonical identity")

    def _validate_completed_view(self, view_path: Path) -> None:
        _require_real_directory(view_path, label="completed Codex attempt view")
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            for path in root.rglob("*"):
                if path.is_symlink() or path.is_file():
                    raise RuntimeError(f"Completed Codex view retains unexpected data: {path}")

    def _promote_view(self, lease: CodexInteractiveSessionLease) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for public_name, store_name, canonical_root in (
            ("sessions", "active", self.active_root),
            ("archived_sessions", "archived", self.archive_root),
        ):
            view_root = lease.view_path / public_name
            for source in list(_rollout_files(view_root)):
                relative = _safe_relative(source, view_root)
                thread_id = _thread_id(source)
                if thread_id is None:
                    raise RuntimeError(f"Rollout lacks a Codex thread id: {source}")
                resume_thread_id = lease.manifest.get("resume_thread_id")
                if resume_thread_id is not None and thread_id != resume_thread_id:
                    raise RuntimeError("Resumed Codex view contains a different thread identity")
                destination = canonical_root / relative
                _ensure_directory_chain(canonical_root, relative.parent)
                if _lexists(destination):
                    if destination.is_symlink() or _identity(source) != _identity(destination):
                        raise RuntimeError(
                            f"Codex rollout collision preserves both files: {destination}"
                        )
                else:
                    try:
                        os.link(source, destination, follow_symlinks=False)
                    except FileExistsError:
                        if _identity(source) != _identity(destination):
                            raise RuntimeError(
                                f"Codex rollout collision preserves both files: {destination}"
                            )
                    if _identity(source) != _identity(destination):
                        raise RuntimeError("Promoted Codex rollout identity mismatch")
                    file_fd = os.open(destination, os.O_RDONLY)
                    try:
                        os.fsync(file_fd)
                    finally:
                        os.close(file_fd)
                    _fsync_directory(destination.parent)
                lease.manifest.update(
                    final_store=store_name,
                    final_relpath=relative.as_posix(),
                )
                self._write_manifest(lease)
                source.unlink()
                _fsync_directory(source.parent)
                rows.append(
                    self._index_row(
                        thread_id=thread_id,
                        launch_id=lease.launch_id,
                        canonical_store=store_name,
                        relative_path=relative,
                    )
                )
        return rows

    def _index_row(
        self,
        *,
        thread_id: str,
        launch_id: str | None,
        canonical_store: str,
        relative_path: Path,
    ) -> dict[str, Any]:
        return {
            "backend_name": "codex",
            "session_id": thread_id,
            "launch_id": launch_id,
            "cwd": "",
            "first_prompt": "",
            "summary": "",
            "git_branch": None,
            "modified": None,
            "is_sidechain": False,
            "session_type_hint": "cook",
            "canonical_store": canonical_store,
            "relative_path": relative_path.as_posix(),
        }

    def _read_index_rows(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            raw = _read_bounded(self.index_path, _INDEX_READ_LIMIT)
            payload = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [dict(row) for row in payload if isinstance(row, Mapping)]

    def _merge_index(self, incoming: Sequence[dict[str, Any]]) -> None:
        lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
        try:
            self._merge_index_unlocked(incoming)
        finally:
            lifecycle.release()

    def _merge_index_unlocked(self, incoming: Sequence[dict[str, Any]]) -> None:
        existing = self._read_index_rows()
        incoming_ids = {
            str(row["session_id"]) for row in incoming if isinstance(row.get("session_id"), str)
        }
        ordered = [dict(row) for row in incoming]
        ordered.extend(
            row
            for row in existing
            if isinstance(row.get("session_id"), str)
            and str(row["session_id"]) not in incoming_ids
        )
        _atomic_json(self.index_path, ordered)

    def read_index(self, cwd: str) -> tuple[SessionSummary, ...]:
        wanted = str(Path(cwd).expanduser().resolve(strict=False))
        summaries: list[SessionSummary] = []
        for row in self._read_index_rows():
            try:
                row_cwd_raw = row.get("cwd")
                row_cwd = (
                    str(Path(str(row_cwd_raw)).expanduser().resolve(strict=False))
                    if row_cwd_raw
                    else ""
                )
                if row_cwd and row_cwd != wanted:
                    continue
                summary = SessionSummary(
                    backend_name=str(row.get("backend_name") or "codex"),
                    session_id=str(row["session_id"]),
                    launch_id=(
                        str(row["launch_id"]) if row.get("launch_id") is not None else None
                    ),
                    cwd=row_cwd or wanted,
                    first_prompt=str(row.get("first_prompt") or ""),
                    summary=str(row.get("summary") or ""),
                    git_branch=(
                        str(row["git_branch"]) if row.get("git_branch") is not None else None
                    ),
                    modified=(str(row["modified"]) if row.get("modified") is not None else None),
                    is_sidechain=bool(row.get("is_sidechain", False)),
                    session_type_hint=(
                        str(row["session_type_hint"])
                        if row.get("session_type_hint") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not summary.is_sidechain:
                summaries.append(summary)
        return tuple(summaries)

    def _locate_with_store(self, thread_id: str) -> tuple[str, Path] | None:
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                if _thread_id(path) == thread_id:
                    return store_name, path
        return None

    def locate_session(self, thread_id: str) -> Path | None:
        located = self._locate_with_store(thread_id)
        if located is not None:
            return located[1]
        for view_path in sorted(self.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR or not view_path.is_dir():
                continue
            for public_name in _INERT_NAMES:
                for path in _rollout_files(view_path / public_name):
                    if _thread_id(path) == thread_id:
                        return path
        return None

    def recover(self) -> None:
        """Recover safely-owned orphan views, then rebuild the derived index."""
        self._ensure_roots()
        for view_path in sorted(self.views_root.iterdir()):
            if (
                view_path.name == _LOCKS_SUBDIR
                or _VIEW_ID_RE.fullmatch(view_path.name) is None
                or view_path.is_symlink()
                or not view_path.is_dir()
            ):
                continue
            lock_path = self.locks_root / f"view-{view_path.name}.lock"
            try:
                view_lock = _FileLease.acquire(lock_path, nonblocking=True)
            except BlockingIOError:
                continue
            try:
                manifest_path = view_path / _MANIFEST_NAME
                if not manifest_path.is_file() or manifest_path.is_symlink():
                    continue
                try:
                    manifest = json.loads(_read_bounded(manifest_path, _MANIFEST_READ_LIMIT))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("schema_version") != 1
                    or manifest.get("view_id") != view_path.name
                    or manifest.get("state") not in _MANIFEST_STATES
                ):
                    continue
                state = manifest.get("state")
                thread_locks: list[_FileLease] = []
                thread_ids = {
                    thread_id
                    for public_name in _INERT_NAMES
                    for path in _rollout_files(view_path / public_name)
                    if (thread_id := _thread_id(path)) is not None
                }
                resume_thread_id = manifest.get("resume_thread_id")
                if isinstance(resume_thread_id, str):
                    thread_ids.add(resume_thread_id)
                try:
                    for thread_id in sorted(thread_ids):
                        thread_locks.append(
                            _FileLease.acquire(
                                self._thread_lock_path(thread_id),
                                nonblocking=True,
                            )
                        )
                except BlockingIOError:
                    for thread_lock in reversed(thread_locks):
                        thread_lock.release()
                    continue
                lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
                try:
                    if state == "complete":
                        self._validate_completed_view(view_path)
                        shutil.rmtree(view_path)
                        _fsync_directory(self.views_root)
                    elif state in {"prepared", "failed"} and manifest.get("child_pid") is None:
                        manifest["state"] = "failed"
                        _atomic_json(manifest_path, manifest)
                        self._validate_pre_spawn_view(
                            view_path,
                            manifest,
                            allow_missing_resume=False,
                        )
                        shutil.rmtree(view_path)
                        _fsync_directory(self.views_root)
                    elif state in {"running", "finalizing"} and manifest.get("reaped") is True:
                        attempt_lease = CodexInteractiveSessionLease(
                            store=self,
                            session_home=Path("/"),
                            launch_id=str(manifest["launch_id"]),
                            attempt=int(manifest["attempt"]),
                            current_resume_spec=(
                                NamedResume(resume_thread_id)
                                if isinstance(resume_thread_id, str)
                                else NoResume()
                            ),
                            view_id=view_path.name,
                            view_path=view_path,
                            manifest=manifest,
                            view_lease=view_lock,
                            inert_targets={},
                        )
                        manifest["state"] = "finalizing"
                        self._write_manifest(attempt_lease)
                        self._promote_view(attempt_lease)
                        manifest["state"] = "complete"
                        self._write_manifest(attempt_lease)
                        self._validate_completed_view(view_path)
                        shutil.rmtree(view_path)
                        _fsync_directory(self.views_root)
                finally:
                    lifecycle.release()
                    for thread_lock in reversed(thread_locks):
                        thread_lock.release()
            finally:
                view_lock.release()
        rows: list[dict[str, Any]] = []
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                thread_id = _thread_id(path)
                if thread_id is None:
                    continue
                rows.append(
                    self._index_row(
                        thread_id=thread_id,
                        launch_id=None,
                        canonical_store=store_name,
                        relative_path=_safe_relative(path, root),
                    )
                )
        lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
        try:
            deduplicated: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                thread_id = str(row["session_id"])
                if thread_id not in seen:
                    seen.add(thread_id)
                    deduplicated.append(row)
            _atomic_json(self.index_path, deduplicated)
        finally:
            lifecycle.release()


__all__ = [
    "CodexInteractiveSessionLease",
    "CodexSessionStore",
    "codex_session_index_path",
]
