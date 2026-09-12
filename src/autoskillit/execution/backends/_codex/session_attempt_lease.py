"""Owned Codex attempt view and durable spawn/reap proof."""

from __future__ import annotations

import os
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    ResumeSpec,
    SessionAttemptHandle,
    get_logger,
    read_boot_id,
    read_pid_namespace_inode,
    read_starttime_ticks,
)
from autoskillit.execution.backends._codex_session_lease import _FileLease
from autoskillit.execution.process import INTERACTIVE_TETHER_CEILING_SECONDS

if TYPE_CHECKING:
    from autoskillit.execution.backends._codex_session_storage import CodexSessionStore

logger = get_logger(__name__)


@dataclass(slots=True)
class CodexSessionAttemptLease(AbstractContextManager[SessionAttemptHandle]):
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
    ceiling_seconds: float = INTERACTIVE_TETHER_CEILING_SECONDS
    _entered: bool = False
    _closed: bool = False

    def __enter__(self) -> SessionAttemptHandle:
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
        return SessionAttemptHandle(
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
        if isinstance(pid, bool) or isinstance(pgid, bool) or pid <= 0 or pgid <= 0:
            raise ValueError("Child pid and pgid must be positive integers")
        spawner_pid = os.getpid()
        self.manifest.update(
            state="running",
            child_pid=pid,
            child_pgid=pgid,
            reaped=False,
            # Optional additions (schema_version stays 1) — make the manifest
            # self-sufficient for recover()'s own verify-before-mark decision,
            # independent of the Phase-1 tether. None off-Linux, matching the
            # underlying /proc readers' own platform gating.
            spawner_pid=spawner_pid,
            spawner_starttime_ticks=read_starttime_ticks(spawner_pid),
            boot_id=read_boot_id(),
            child_starttime_ticks=read_starttime_ticks(pid),
            pidns_inode=read_pid_namespace_inode(pid),
            not_after=time.time() + self.ceiling_seconds,
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

    def _release_thread_lease(self, failures: list[BaseException]) -> bool:
        released = True
        if self.thread_lease is not None:
            try:
                self.thread_lease.release()
            except BaseException as release_error:
                logger.error("codex_thread_lease_release_failed", exc_info=True)
                failures.append(release_error)
                released = False
        return released

    def _release_view_lease(self, failures: list[BaseException]) -> None:
        try:
            self.view_lease.release()
        except BaseException as release_error:
            logger.error("codex_view_lease_release_failed", exc_info=True)
            failures.append(release_error)

    def _release_leases(self, failures: list[BaseException]) -> None:
        self._release_thread_lease(failures)
        self._release_view_lease(failures)

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
        parent_session_ids: tuple[str, ...] = ()
        cleanup_succeeded = False
        try:
            parent_session_ids = self.store._exit_attempt(self)
            cleanup_succeeded = True
        except BaseException as cleanup_error:
            logger.error(
                "codex_attempt_exit_failed",
                view_id=self.view_id,
                error_type=type(cleanup_error).__name__,
            )
            failures.append(cleanup_error)
        thread_released = self._release_thread_lease(failures)
        try:
            if cleanup_succeeded and thread_released and parent_session_ids:
                self.store._publish_completed_view(self.view_path, parent_session_ids)
        except BaseException as publication_cleanup_error:
            logger.error("codex_attempt_publication_cleanup_failed", exc_info=True)
            failures.append(publication_cleanup_error)
        finally:
            self._release_view_lease(failures)
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
