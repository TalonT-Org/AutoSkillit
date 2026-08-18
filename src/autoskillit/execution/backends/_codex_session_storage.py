"""Private durable storage for interactive Codex rollout views."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import stat
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import (
    CODEX_ACTIVE_VIEWS_SUBDIR,
    CODEX_ARCHIVED_SESSIONS_SUBDIR,
    CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
    CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
    CODEX_SESSIONS_SUBDIR,
    BareResume,
    CookSessionHandle,
    NamedResume,
    NoResume,
    ResumeSpec,
    SessionSummary,
    default_log_dir,
    get_logger,
)
from autoskillit.execution.backends._codex_fs_atomic import (
    _atomic_json,
    _ensure_directory_chain,
    _filesystem_type,
    _fsync_directory,
    _lexists,
    _read_bounded,
    _replace_symlink,
    _require_real_directory,
    _write_reconciliation_audit,
)
from autoskillit.execution.backends._codex_parse import (
    _identity,
    _preserves_rollout_prefix,
    _rollout_cwd,
    _rollout_files,
    _safe_relative,
    _safe_relative_value,
    _thread_id,
)

_VIEW_ID_RE = re.compile(r"^[0-9a-f]{16}-[1-9][0-9]*$")
_LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_INDEX_READ_LIMIT = 4 * 1024 * 1024
_MANIFEST_READ_LIMIT = 256 * 1024
_MANIFEST_NAME = "manifest.json"
_LOCKS_SUBDIR = ".locks"
_INDEX_NAME = "codex-session-index.json"
_RECONCILIATION_AUDIT_SCHEMA_VERSION = 1
logger = get_logger(__name__)
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


def codex_session_index_path(log_dir: Path | None = None) -> Path:
    """Return the one production path for the derived Codex cook index."""
    root = default_log_dir() if log_dir is None else Path(log_dir)
    return root.expanduser().resolve(strict=False) / _INDEX_NAME


@dataclass(slots=True)
class _FileLease:
    path: Path
    fd: int = field(init=False)

    @classmethod
    def acquire(cls, lock_path: Path, *, nonblocking: bool = False) -> _FileLease:
        if lock_path.suffix != ".lock":
            raise ValueError(f"Lock path must use the .lock suffix: {lock_path}")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _require_real_directory(lock_path.parent, label="lock directory")
        if _lexists(lock_path) and lock_path.is_symlink():
            raise RuntimeError(f"Refusing symlink lock file: {lock_path}")
        instance = cls(path=lock_path)
        instance.fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            if not stat.S_ISREG(os.fstat(instance.fd).st_mode):
                raise RuntimeError(f"Lock path is not a regular file: {lock_path}")
            fcntl.flock(instance.fd, operation)
            owner = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired_ns": time.time_ns(),
            }
            os.ftruncate(instance.fd, 0)
            os.write(instance.fd, json.dumps(owner, sort_keys=True).encode())
            os.fsync(instance.fd)
        except BaseException:
            fd, instance.fd = instance.fd, -1
            os.close(fd)
            raise
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
            logger.error("codex_attempt_entry_failed", exc_info=True)
            self._closed = True
            failures: list[BaseException] = [entry_error]
            try:
                self.store._abort_pre_spawn(self)
            except BaseException as cleanup_error:
                logger.error("codex_attempt_entry_rollback_failed", exc_info=True)
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
                logger.error("codex_thread_lease_release_failed", exc_info=True)
                failures.append(release_error)
        try:
            self.view_lease.release()
        except BaseException as release_error:
            logger.error("codex_view_lease_release_failed", exc_info=True)
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
            logger.error(
                "codex_attempt_exit_failed",
                view_id=self.view_id,
                error_type=type(cleanup_error).__name__,
            )
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
        self.reconciliations_root = self.log_dir / CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR
        self.reconciliation_tombstones_root = (
            self.log_dir / CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR
        )
        self.locks_root = self.views_root / _LOCKS_SUBDIR
        self.index_path = (
            Path(index_path).expanduser().resolve(strict=False)
            if index_path is not None
            else codex_session_index_path(self.log_dir)
        )

    def _ensure_roots(self) -> None:
        roots = (
            self.active_root,
            self.archive_root,
            self.views_root,
            self.locks_root,
            self.reconciliations_root,
            self.reconciliation_tombstones_root,
        )
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
            _require_real_directory(root, label="Codex storage root")
        devices = {
            self.active_root.stat().st_dev,
            self.archive_root.stat().st_dev,
            self.views_root.stat().st_dev,
            self.reconciliations_root.stat().st_dev,
            self.reconciliation_tombstones_root.stat().st_dev,
        }
        if len(devices) != 1:
            raise RuntimeError("Codex rollout stores and views must share one filesystem")
        filesystem_types = {
            _filesystem_type(self.active_root),
            _filesystem_type(self.archive_root),
            _filesystem_type(self.views_root),
            _filesystem_type(self.reconciliations_root),
            _filesystem_type(self.reconciliation_tombstones_root),
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
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> CodexInteractiveSessionLease:
        self._ensure_roots()
        view_id = f"{launch_id}-{attempt}"
        if _VIEW_ID_RE.fullmatch(view_id) is None:
            raise ValueError(f"Invalid Codex attempt identity: {view_id!r}")
        session_home = Path(session_home).resolve(strict=True)
        project_path = Path(project_dir)
        if not project_path.is_absolute():
            raise ValueError("Codex project directory must be absolute")
        resolved_project = project_path.resolve(strict=True)
        if resolved_project != project_path or not resolved_project.is_dir():
            raise ValueError("Codex project directory must be canonical")
        inert_targets = self._validate_inert_home(session_home)
        view_path = self.views_root / view_id
        view_lease = _FileLease.acquire(self.locks_root / f"view-{view_id}.lock")
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "launch_id": launch_id,
            "attempt": attempt,
            "view_id": view_id,
            "project_cwd": str(resolved_project),
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
        view_created = False
        try:
            if os.path.lexists(view_path):
                raise FileExistsError(f"Codex attempt view already exists: {view_id}")
            view_path.mkdir(mode=0o700)
            view_created = True
            (view_path / "sessions").mkdir()
            (view_path / "archived_sessions").mkdir()
            _fsync_directory(view_path)
            _fsync_directory(self.views_root)
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
            if view_created and _lexists(view_path):
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
        candidates: list[tuple[str, Path, Path, str]] = []
        for public_name, store_name in _PUBLIC_TO_STORE.items():
            view_root = lease.view_path / public_name
            for source in _rollout_files(view_root):
                relative = _safe_relative(source, view_root)
                thread_id = _thread_id(source)
                if thread_id is None:
                    raise RuntimeError(f"Rollout lacks a Codex thread id: {source}")
                candidates.append((store_name, relative, source, thread_id))

        resume_thread_id = lease.manifest.get("resume_thread_id")
        if isinstance(resume_thread_id, str) and any(
            thread_id != resume_thread_id for _, _, _, thread_id in candidates
        ):
            raise RuntimeError("Resumed Codex view contains a different thread identity")

        final_store = lease.manifest.get("final_store")
        final_relpath_value = lease.manifest.get("final_relpath")
        if (final_store is None) != (final_relpath_value is None):
            raise RuntimeError("Codex final rollout metadata is incomplete")

        selected_source: Path | None = None
        if isinstance(final_store, str) and isinstance(final_relpath_value, str):
            if final_store not in _STORE_TO_PUBLIC:
                raise RuntimeError(f"Invalid final Codex store: {final_store!r}")
            final_relative = _safe_relative_value(final_relpath_value)
            for store_name, relative, source, _ in candidates:
                if store_name == final_store and relative == final_relative:
                    selected_source = source
                    break
        else:
            if not candidates:
                raise RuntimeError("Codex attempt has no rollout data to promote")
            selectable = candidates
            resume_store = lease.manifest.get("resume_source_store")
            resume_relpath = lease.manifest.get("resume_source_relpath")
            if isinstance(resume_store, str) and isinstance(resume_relpath, str):
                transitioned = [
                    candidate
                    for candidate in candidates
                    if (candidate[0], candidate[1].as_posix()) != (resume_store, resume_relpath)
                ]
                if transitioned:
                    selectable = transitioned
            unique_locations = {
                (store_name, relative.as_posix()) for store_name, relative, _, _ in selectable
            }
            if len(unique_locations) != 1:
                raise RuntimeError("Codex rollout transition is ambiguous; preserving staged data")
            final_store, final_relative, selected_source, _ = selectable[0]

        canonical_root = self.active_root if final_store == "active" else self.archive_root
        destination = canonical_root / final_relative
        _ensure_directory_chain(canonical_root, final_relative.parent)
        if selected_source is None and not _lexists(destination):
            raise RuntimeError("Final Codex rollout is missing from staging and canonical storage")

        comparison_source = selected_source if selected_source is not None else destination
        destination_thread_id = _thread_id(comparison_source)
        if destination_thread_id is None:
            raise RuntimeError("Final Codex rollout lacks a thread identity")
        if isinstance(resume_thread_id, str) and destination_thread_id != resume_thread_id:
            raise RuntimeError("Final Codex rollout changed thread identity")

        for _, _, source, thread_id in candidates:
            if thread_id != destination_thread_id:
                raise RuntimeError("Codex view contains multiple thread identities")
            if not _preserves_rollout_prefix(source, comparison_source):
                raise RuntimeError("Codex rollout transition would discard staged rollout content")

        canonical_matches = self._canonical_matches(destination_thread_id)
        obsolete_canonical: list[Path] = []
        for _, canonical in canonical_matches:
            if canonical == destination:
                continue
            if not _preserves_rollout_prefix(canonical, comparison_source):
                raise RuntimeError(
                    "Codex rollout transition would discard canonical rollout content"
                )
            obsolete_canonical.append(canonical)

        if selected_source is not None:
            if _lexists(destination):
                if destination.is_symlink() or _identity(selected_source) != _identity(
                    destination
                ):
                    raise RuntimeError(
                        f"Codex rollout collision preserves both files: {destination}"
                    )
            else:
                try:
                    os.link(selected_source, destination, follow_symlinks=False)
                except FileExistsError:
                    if _identity(selected_source) != _identity(destination):
                        raise RuntimeError(
                            f"Codex rollout collision preserves both files: {destination}"
                        )
                if _identity(selected_source) != _identity(destination):
                    raise RuntimeError("Promoted Codex rollout identity mismatch")
                file_fd = os.open(destination, os.O_RDONLY)
                try:
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                _fsync_directory(destination.parent)

        destination_thread_id = _thread_id(destination)
        if destination_thread_id is None:
            raise RuntimeError("Final Codex rollout lacks a thread identity")
        if isinstance(resume_thread_id, str) and destination_thread_id != resume_thread_id:
            raise RuntimeError("Final Codex rollout changed thread identity")

        if lease.manifest.get("final_store") is None:
            lease.manifest.update(
                final_store=final_store,
                final_relpath=final_relative.as_posix(),
            )
            self._write_manifest(lease)

        for canonical in obsolete_canonical:
            if _lexists(canonical):
                canonical.unlink()
                _fsync_directory(canonical.parent)

        for _, _, source, _ in candidates:
            if _lexists(source):
                source.unlink()
                _fsync_directory(source.parent)

        remaining = [
            path
            for public_name in _INERT_NAMES
            for path in _rollout_files(lease.view_path / public_name)
        ]
        if remaining:
            raise RuntimeError("Codex view retains rollout data after promotion")

        return [
            self._index_row(
                thread_id=destination_thread_id,
                launch_id=lease.launch_id,
                cwd=str(lease.manifest["project_cwd"]),
                canonical_store=final_store,
                relative_path=final_relative,
            )
        ]

    def _index_row(
        self,
        *,
        thread_id: str,
        launch_id: str | None,
        cwd: str,
        canonical_store: str,
        relative_path: Path,
    ) -> dict[str, Any]:
        return {
            "backend_name": "codex",
            "session_id": thread_id,
            "launch_id": launch_id,
            "cwd": cwd,
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
        existing_by_id = {
            str(row["session_id"]): row
            for row in existing
            if isinstance(row.get("session_id"), str)
        }
        incoming_ids = {
            str(row["session_id"]) for row in incoming if isinstance(row.get("session_id"), str)
        }
        if len(incoming_ids) != len(incoming):
            raise RuntimeError("Codex index update contains duplicate or invalid session ids")
        ordered: list[dict[str, Any]] = []
        for row in incoming:
            merged = dict(row)
            session_id = str(merged["session_id"])
            previous = existing_by_id.get(session_id)
            if (
                merged.get("launch_id") is None
                and previous is not None
                and isinstance(previous.get("launch_id"), str)
            ):
                merged["launch_id"] = previous["launch_id"]
            ordered.append(merged)
        ordered.extend(
            row
            for row in existing
            if isinstance(row.get("session_id"), str)
            and str(row["session_id"]) not in incoming_ids
        )
        _atomic_json(self.index_path, ordered)

    def _rebuild_index_unlocked(self) -> None:
        existing_by_id = {
            str(row["session_id"]): row
            for row in self._read_index_rows()
            if isinstance(row.get("session_id"), str)
        }
        rebuilt: list[dict[str, Any]] = []
        seen: dict[str, Path] = {}
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                thread_id = _thread_id(path)
                cwd = _rollout_cwd(path)
                if thread_id is None or cwd is None:
                    continue
                previous_path = seen.get(thread_id)
                if previous_path is not None:
                    raise RuntimeError(
                        "Cannot rebuild Codex index from ambiguous canonical "
                        f"representations: {previous_path}, {path}"
                    )
                seen[thread_id] = path
                existing = existing_by_id.get(thread_id)
                launch_id = (
                    str(existing["launch_id"])
                    if existing is not None and isinstance(existing.get("launch_id"), str)
                    else None
                )
                rebuilt.append(
                    self._index_row(
                        thread_id=thread_id,
                        launch_id=launch_id,
                        cwd=cwd,
                        canonical_store=store_name,
                        relative_path=_safe_relative(path, root),
                    )
                )
        _atomic_json(self.index_path, rebuilt)

    def read_index(self, cwd: str) -> tuple[SessionSummary, ...]:
        wanted = str(Path(cwd).expanduser().resolve(strict=False))
        summaries: list[SessionSummary] = []
        for row in self._read_index_rows():
            try:
                row_cwd_raw = row.get("cwd")
                if not isinstance(row_cwd_raw, str) or not row_cwd_raw:
                    continue
                row_cwd = str(Path(row_cwd_raw).expanduser().resolve(strict=False))
                if row_cwd != wanted:
                    continue
                summary = SessionSummary(
                    backend_name=str(row.get("backend_name") or "codex"),
                    session_id=str(row["session_id"]),
                    launch_id=(
                        str(row["launch_id"]) if row.get("launch_id") is not None else None
                    ),
                    cwd=row_cwd,
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

    def _canonical_matches(self, thread_id: str) -> list[tuple[str, Path]]:
        matches: list[tuple[str, Path]] = []
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                if _thread_id(path) == thread_id:
                    matches.append((store_name, path))
        return matches

    def _locate_with_store(self, thread_id: str) -> tuple[str, Path] | None:
        matches = self._canonical_matches(thread_id)
        if len(matches) > 1:
            locations = ", ".join(str(path) for _, path in matches)
            raise RuntimeError(
                f"Ambiguous canonical Codex rollout representations for {thread_id}: {locations}"
            )
        return matches[0] if matches else None

    def locate_session(self, thread_id: str) -> Path | None:
        located = self._locate_with_store(thread_id)
        if located is not None:
            return located[1]
        if not self.views_root.is_dir():
            return None
        for view_path in sorted(self.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR or not view_path.is_dir():
                continue
            try:
                manifest = json.loads(
                    _read_bounded(view_path / _MANIFEST_NAME, _MANIFEST_READ_LIMIT)
                )
                if not isinstance(manifest, dict):
                    continue
                self._validate_manifest(view_path, manifest)
                if manifest["state"] not in {"running", "finalizing"}:
                    continue
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                continue
            for public_name in _INERT_NAMES:
                for path in _rollout_files(view_path / public_name):
                    if _thread_id(path) == thread_id:
                        return path
        return None

    def _validate_manifest(
        self,
        view_path: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        _require_real_directory(view_path, label="Codex recovery view")
        manifest_path = view_path / _MANIFEST_NAME
        try:
            manifest_mode = manifest_path.lstat().st_mode
        except FileNotFoundError as exc:
            raise RuntimeError("Codex recovery view has no manifest") from exc
        if stat.S_ISLNK(manifest_mode) or not stat.S_ISREG(manifest_mode):
            raise RuntimeError("Codex recovery manifest must be a regular non-symlink file")
        expected_entries = {"sessions", "archived_sessions", _MANIFEST_NAME}
        if {entry.name for entry in view_path.iterdir()} != expected_entries:
            raise RuntimeError("Codex recovery view has an invalid root layout")
        for public_name in _INERT_NAMES:
            _require_real_directory(
                view_path / public_name,
                label=f"Codex recovery {public_name} root",
            )

        if manifest.get("schema_version") != 1:
            raise RuntimeError("Unsupported Codex recovery manifest schema")
        launch_id = manifest.get("launch_id")
        attempt = manifest.get("attempt")
        view_id = manifest.get("view_id")
        if not isinstance(launch_id, str) or _LAUNCH_ID_RE.fullmatch(launch_id) is None:
            raise RuntimeError("Codex recovery manifest has an invalid launch id")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
            raise RuntimeError("Codex recovery manifest has an invalid attempt")
        expected_view_id = f"{launch_id}-{attempt}"
        if view_id != expected_view_id or view_path.name != expected_view_id:
            raise RuntimeError("Codex recovery manifest identity is inconsistent")

        project_cwd = manifest.get("project_cwd")
        if not isinstance(project_cwd, str):
            raise RuntimeError("Codex recovery manifest has no project discriminator")
        project_path = Path(project_cwd)
        if (
            not project_path.is_absolute()
            or str(project_path.expanduser().resolve(strict=False)) != project_cwd
        ):
            raise RuntimeError("Codex recovery project discriminator is not canonical")

        state = manifest.get("state")
        if state not in _MANIFEST_STATES:
            raise RuntimeError("Codex recovery manifest has an invalid lifecycle state")
        child_pid = manifest.get("child_pid")
        child_pgid = manifest.get("child_pgid")
        child_values = (child_pid, child_pgid)
        child_absent = child_values == (None, None)
        child_valid = all(
            not isinstance(value, bool) and isinstance(value, int) and value > 0
            for value in child_values
        )
        if not child_absent and not child_valid:
            raise RuntimeError("Codex recovery child identity is incomplete")

        reaped = manifest.get("reaped")
        if not isinstance(reaped, bool):
            raise RuntimeError("Codex recovery reap proof is not boolean")
        reaped_ns = manifest.get("reaped_ns")
        if reaped:
            if not child_valid:
                raise RuntimeError("Codex recovery reap proof has no matching child")
            if isinstance(reaped_ns, bool) or not isinstance(reaped_ns, int) or reaped_ns <= 0:
                raise RuntimeError("Codex recovery reap timestamp is invalid")
        elif reaped_ns is not None:
            raise RuntimeError("Codex recovery has a reap timestamp without proof")

        resume_thread_id = manifest.get("resume_thread_id")
        resume_store = manifest.get("resume_source_store")
        resume_relpath = manifest.get("resume_source_relpath")
        resume_values = (resume_thread_id, resume_store, resume_relpath)
        if resume_values != (None, None, None):
            if (
                not isinstance(resume_thread_id, str)
                or _THREAD_ID_RE.fullmatch(resume_thread_id) is None
                or resume_store not in _STORE_TO_PUBLIC
                or not isinstance(resume_relpath, str)
            ):
                raise RuntimeError("Codex recovery resume metadata is incomplete")
            _safe_relative_value(resume_relpath)

        final_store = manifest.get("final_store")
        final_relpath = manifest.get("final_relpath")
        if (final_store is None) != (final_relpath is None):
            raise RuntimeError("Codex recovery final metadata is incomplete")
        if final_store is not None:
            if final_store not in _STORE_TO_PUBLIC or not isinstance(final_relpath, str):
                raise RuntimeError("Codex recovery final metadata is invalid")
            final_relative = _safe_relative_value(final_relpath)
            if state not in {"finalizing", "complete"}:
                raise RuntimeError("Codex recovery final metadata precedes finalization")
            if state == "complete":
                canonical_root = self.active_root if final_store == "active" else self.archive_root
                canonical = canonical_root / final_relative
                _safe_relative(canonical, canonical_root)
                final_thread_id = _thread_id(canonical)
                if final_thread_id is None or (
                    isinstance(resume_thread_id, str) and final_thread_id != resume_thread_id
                ):
                    raise RuntimeError("Codex recovery final rollout identity is invalid")
        elif state == "complete":
            raise RuntimeError("Complete Codex recovery view has no final rollout metadata")

        staged_rollouts: list[tuple[str, Path, Path, str]] = []
        for public_name, store_name in _PUBLIC_TO_STORE.items():
            staged_root = view_path / public_name
            for staged in _rollout_files(staged_root):
                relative = _safe_relative(staged, staged_root)
                thread_id = _thread_id(staged)
                if thread_id is None or _THREAD_ID_RE.fullmatch(thread_id) is None:
                    raise RuntimeError("Codex recovery staged rollout has no valid thread id")
                staged_rollouts.append((store_name, relative, staged, thread_id))

        staged_thread_ids = {item[3] for item in staged_rollouts}
        if len(staged_thread_ids) > 1:
            raise RuntimeError("Codex recovery view contains multiple thread identities")
        if isinstance(resume_thread_id, str) and any(
            thread_id != resume_thread_id for *_, thread_id in staged_rollouts
        ):
            raise RuntimeError("Codex recovery resume view changed thread identity")

        if isinstance(resume_store, str) and isinstance(resume_relpath, str):
            resume_root = self.active_root if resume_store == "active" else self.archive_root
            resume_relative = _safe_relative_value(resume_relpath)
            canonical_resume = resume_root / resume_relative
            if state in {"prepared", "running"}:
                _safe_relative(canonical_resume, resume_root)
                if not staged_rollouts or not any(
                    _preserves_rollout_prefix(canonical_resume, staged)
                    for _, _, staged, _ in staged_rollouts
                ):
                    raise RuntimeError(
                        "Codex recovery resume view does not preserve its canonical source"
                    )

        if isinstance(final_store, str) and isinstance(final_relpath, str):
            final_root = self.active_root if final_store == "active" else self.archive_root
            final_relative = _safe_relative_value(final_relpath)
            canonical_final = final_root / final_relative
            staged_final = view_path / _STORE_TO_PUBLIC[final_store] / final_relative
            final_candidates = [path for path in (staged_final, canonical_final) if _lexists(path)]
            if not final_candidates:
                raise RuntimeError("Codex recovery final rollout data is missing")
            for final_candidate in final_candidates:
                expected_root = (
                    view_path / _STORE_TO_PUBLIC[final_store]
                    if final_candidate == staged_final
                    else final_root
                )
                _safe_relative(final_candidate, expected_root)
                final_thread_id = _thread_id(final_candidate)
                if final_thread_id is None or (
                    isinstance(resume_thread_id, str) and final_thread_id != resume_thread_id
                ):
                    raise RuntimeError("Codex recovery final rollout identity is invalid")

        if state == "prepared" and (not child_absent or reaped):
            raise RuntimeError("Prepared Codex recovery view has child lifecycle data")
        if state in {"running", "finalizing", "complete"} and not child_valid:
            raise RuntimeError("Spawned Codex recovery view has no child identity")
        if state in {"finalizing", "complete"} and not reaped:
            raise RuntimeError("Final Codex recovery view has no reap proof")

    def _read_reconciliation_candidate(
        self,
        view_path: Path,
    ) -> tuple[bytes, dict[str, Any]]:
        raw_manifest = _read_bounded(view_path / _MANIFEST_NAME, _MANIFEST_READ_LIMIT)
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex reconciliation manifest is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("Codex reconciliation manifest is not an object")
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            with os.scandir(root) as entries:
                if next(entries, None) is not None:
                    raise RuntimeError(
                        f"Codex reconciliation {public_name} root is not strictly empty"
                    )
        self._validate_manifest(view_path, manifest)
        if manifest["state"] not in {"running", "finalizing", "failed"}:
            raise RuntimeError("Codex view is not a retained schema-v1 unknown attempt")
        return raw_manifest, manifest

    @staticmethod
    def _reconciliation_thread_ids(manifest: Mapping[str, Any]) -> set[str]:
        resume_thread_id = manifest.get("resume_thread_id")
        return {resume_thread_id} if isinstance(resume_thread_id, str) else set()

    def _read_reconciliation_audit(self, path: Path, *, view_id: str) -> dict[str, Any]:
        try:
            payload = json.loads(_read_bounded(path, _MANIFEST_READ_LIMIT))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid reconciliation audit for {view_id}") from exc
        expected_keys = {
            "schema_version",
            "view_id",
            "recorded_at",
            "reason",
            "manifest_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise RuntimeError(f"Invalid reconciliation audit contract for {view_id}")
        recorded_at = payload.get("recorded_at")
        reason = payload.get("reason")
        digest = payload.get("manifest_sha256")
        if (
            payload.get("schema_version") != _RECONCILIATION_AUDIT_SCHEMA_VERSION
            or payload.get("view_id") != view_id
            or not isinstance(recorded_at, str)
            or not isinstance(reason, str)
            or reason.strip() != reason
            or not reason
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise RuntimeError(f"Invalid reconciliation audit values for {view_id}")
        try:
            timestamp = datetime.fromisoformat(recorded_at)
        except ValueError as exc:
            raise RuntimeError(f"Invalid reconciliation audit timestamp for {view_id}") from exc
        if timestamp.tzinfo is None:
            raise RuntimeError(f"Reconciliation audit timestamp lacks timezone for {view_id}")
        return payload

    def list_retained_attempt_views(self) -> tuple[dict[str, Any], ...]:
        """List retained Codex attempt views without recovery or mutation."""
        self._ensure_roots()
        rows: list[dict[str, Any]] = []
        for view_path in sorted(self.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR:
                continue
            state: object = None
            try:
                if _VIEW_ID_RE.fullmatch(view_path.name) is None:
                    raise RuntimeError("invalid view id")
                _raw_manifest, manifest = self._read_reconciliation_candidate(view_path)
                state = manifest["state"]
            except (OSError, RuntimeError, ValueError) as exc:
                rows.append(
                    {
                        "view_id": view_path.name,
                        "state": state,
                        "eligible": False,
                        "detail": str(exc),
                    }
                )
            else:
                rows.append(
                    {
                        "view_id": view_path.name,
                        "state": state,
                        "eligible": True,
                        "detail": "retained schema-v1 unknown with empty staged roots",
                    }
                )
        return tuple(rows)

    def _delete_reconciliation_tombstone(self, tombstone_path: Path) -> None:
        _require_real_directory(tombstone_path, label="Codex reconciliation tombstone")
        shutil.rmtree(tombstone_path)
        _fsync_directory(self.reconciliation_tombstones_root)

    def discard_attempt_view(self, view_id: str, reason: str) -> dict[str, Any]:
        """Explicitly reconcile one eligible retained schema-v1 unknown view."""
        if _VIEW_ID_RE.fullmatch(view_id) is None:
            raise ValueError(f"Invalid Codex attempt view id: {view_id!r}")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("Codex attempt reconciliation requires a non-empty reason")
        self._ensure_roots()
        view_path = self.views_root / view_id
        audit_path = self.reconciliations_root / f"{view_id}.json"
        tombstone_path = self.reconciliation_tombstones_root / view_id
        view_lock = _FileLease.acquire(
            self.locks_root / f"view-{view_id}.lock",
            nonblocking=True,
        )
        thread_locks: list[_FileLease] = []
        lifecycle: _FileLease | None = None
        audit: dict[str, Any] | None = None
        delete_tombstone = False
        try:
            view_exists = _lexists(view_path)
            tombstone_exists = _lexists(tombstone_path)
            audit_exists = _lexists(audit_path)
            if view_exists and tombstone_exists:
                raise RuntimeError(f"Conflicting view and tombstone retained for {view_id}")
            if tombstone_exists:
                if not audit_exists:
                    raise RuntimeError(f"Tombstone has no reconciliation audit for {view_id}")
                _require_real_directory(tombstone_path, label="Codex reconciliation tombstone")
                audit = self._read_reconciliation_audit(audit_path, view_id=view_id)
                if audit["reason"] != normalized_reason:
                    raise RuntimeError(f"Reconciliation reason conflicts for {view_id}")
                lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
                if _lexists(view_path) or not _lexists(tombstone_path):
                    raise RuntimeError(f"Reconciliation tombstone changed for {view_id}")
                delete_tombstone = True
            elif not view_exists:
                if not audit_exists:
                    raise FileNotFoundError(f"Codex attempt view not found: {view_id}")
                audit = self._read_reconciliation_audit(audit_path, view_id=view_id)
                if audit["reason"] != normalized_reason:
                    raise RuntimeError(f"Reconciliation reason conflicts for {view_id}")
            else:
                initial_raw, initial_manifest = self._read_reconciliation_candidate(view_path)
                initial_digest = hashlib.sha256(initial_raw).hexdigest()
                initial_thread_ids = self._reconciliation_thread_ids(initial_manifest)
                for thread_id in sorted(initial_thread_ids):
                    thread_locks.append(
                        _FileLease.acquire(self._thread_lock_path(thread_id), nonblocking=True)
                    )
                lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
                if _lexists(tombstone_path):
                    raise RuntimeError(f"Reconciliation tombstone appeared for {view_id}")
                final_raw, final_manifest = self._read_reconciliation_candidate(view_path)
                final_digest = hashlib.sha256(final_raw).hexdigest()
                if (
                    final_digest != initial_digest
                    or self._reconciliation_thread_ids(final_manifest) != initial_thread_ids
                ):
                    raise RuntimeError(
                        f"Codex attempt view changed during reconciliation: {view_id}"
                    )
                if audit_exists:
                    audit = self._read_reconciliation_audit(audit_path, view_id=view_id)
                    if (
                        audit["reason"] != normalized_reason
                        or audit["manifest_sha256"] != final_digest
                    ):
                        raise RuntimeError(f"Reconciliation audit conflicts for {view_id}")
                else:
                    audit = {
                        "schema_version": _RECONCILIATION_AUDIT_SCHEMA_VERSION,
                        "view_id": view_id,
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "reason": normalized_reason,
                        "manifest_sha256": final_digest,
                    }
                    _write_reconciliation_audit(audit_path, audit)
                os.rename(view_path, tombstone_path)
                _fsync_directory(self.views_root)
                _fsync_directory(self.reconciliation_tombstones_root)
                delete_tombstone = True
        finally:
            if lifecycle is not None:
                lifecycle.release()
            for thread_lock in reversed(thread_locks):
                thread_lock.release()
            view_lock.release()

        if delete_tombstone:
            self._delete_reconciliation_tombstone(tombstone_path)
        if audit is None:
            raise RuntimeError(f"Reconciliation did not produce an audit for {view_id}")
        return dict(audit)

    def recover(self) -> None:
        """Recover safely-owned orphan views, then rebuild the derived index."""
        self._ensure_roots()
        failures: list[BaseException] = []
        for view_path in sorted(self.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR:
                continue
            if (
                _VIEW_ID_RE.fullmatch(view_path.name) is None
                or view_path.is_symlink()
                or not view_path.is_dir()
            ):
                failures.append(RuntimeError(f"Invalid Codex recovery view retained: {view_path}"))
                continue
            lock_path = self.locks_root / f"view-{view_path.name}.lock"
            try:
                view_lock = _FileLease.acquire(lock_path, nonblocking=True)
            except BlockingIOError:
                continue
            try:
                try:
                    manifest_path = view_path / _MANIFEST_NAME
                    manifest = json.loads(_read_bounded(manifest_path, _MANIFEST_READ_LIMIT))
                    if not isinstance(manifest, dict):
                        raise RuntimeError("Codex recovery manifest is not an object")
                    self._validate_manifest(view_path, manifest)
                except BaseException as exc:
                    logger.error("codex_recovery_manifest_invalid", exc_info=True)
                    failures.append(
                        RuntimeError(
                            f"Invalid Codex recovery manifest retained for {view_path.name}: {exc}"
                        )
                    )
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
                        self._validate_pre_spawn_view(
                            view_path,
                            manifest,
                            allow_missing_resume=False,
                        )
                        shutil.rmtree(view_path)
                        _fsync_directory(self.views_root)
                    elif state in {"running", "finalizing", "failed"}:
                        if manifest.get("reaped") is not True:
                            manifest["reaped"] = True
                            manifest["reaped_ns"] = time.time_ns()
                            _atomic_json(manifest_path, manifest)
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
                        recovered_rows = self._promote_view(attempt_lease)
                        self._merge_index_unlocked(recovered_rows)
                        manifest["state"] = "complete"
                        self._write_manifest(attempt_lease)
                        self._validate_completed_view(view_path)
                        shutil.rmtree(view_path)
                        _fsync_directory(self.views_root)
                    else:
                        raise RuntimeError(f"Unsupported Codex recovery state retained: {state!r}")
                except BaseException as exc:
                    logger.error("codex_recovery_view_failed", exc_info=True)
                    failures.append(
                        RuntimeError(f"Codex recovery failed closed for {view_path.name}: {exc}")
                    )
                finally:
                    lifecycle.release()
                    for thread_lock in reversed(thread_locks):
                        thread_lock.release()
            finally:
                view_lock.release()
        lifecycle = _FileLease.acquire(self.locks_root / "lifecycle.lock")
        try:
            self._rebuild_index_unlocked()
        except BaseException as exc:
            logger.error("codex_recovery_index_rebuild_failed", exc_info=True)
            failures.append(RuntimeError(f"Codex recovery index rebuild failed closed: {exc}"))
        finally:
            lifecycle.release()
        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("Codex history recovery failed closed", failures)


__all__ = [
    "CodexInteractiveSessionLease",
    "CodexSessionStore",
    "codex_session_index_path",
]
