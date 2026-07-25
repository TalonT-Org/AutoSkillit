"""Interprocess serialization for shared Codex configuration writes."""

from __future__ import annotations

import errno
import fcntl
import json
import math
import os
import socket
import stat
import threading
import time
from pathlib import Path
from types import TracebackType

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05
_OWNER_DIAGNOSTIC_LIMIT = 4096
_owned_paths: dict[Path, int] = {}
_owned_paths_guard = threading.Lock()


class CodexConfigLock:
    """Exclusive canonical-path lock for a shared Codex ``config.toml``.

    The sidecar is stable across atomic config replacement. Acquisitions are
    deliberately non-reentrant within a process so composed transactions must
    call unlocked leaf mutators instead of nesting public writer facades.
    """

    def __init__(
        self,
        config_path: Path,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite non-negative number")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be a finite positive number")
        self.config_path = Path(config_path).expanduser().resolve(strict=False)
        self.lock_path = self.config_path.with_name(f".{self.config_path.name}.autoskillit.lock")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd: int | None = None
        self._owner_pid: int | None = None

    def _claim_process_ownership(self) -> int:
        pid = os.getpid()
        with _owned_paths_guard:
            owner_pid = _owned_paths.get(self.config_path)
            if owner_pid == pid:
                raise RuntimeError(
                    "Codex config lock is non-reentrant; "
                    f"process {pid} already owns {self.config_path}"
                )
            _owned_paths[self.config_path] = pid
        return pid

    def _release_process_ownership(self, pid: int) -> None:
        with _owned_paths_guard:
            if _owned_paths.get(self.config_path) == pid:
                del _owned_paths[self.config_path]

    def _write_owner_diagnostics(self, fd: int, pid: int) -> None:
        payload = json.dumps(
            {
                "acquired_at_unix": time.time(),
                "config_path": str(self.config_path),
                "hostname": socket.gethostname(),
                "pid": pid,
                "thread_id": threading.get_ident(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        os.ftruncate(fd, 0)
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)

    def _owner_diagnostics(self, fd: int) -> str:
        try:
            raw = os.pread(fd, _OWNER_DIAGNOSTIC_LIMIT, 0)
        except OSError as exc:
            return f"unavailable ({type(exc).__name__}: {exc})"
        if not raw:
            return "unavailable"
        return raw.decode("utf-8", errors="replace")

    def acquire(self) -> CodexConfigLock:
        """Acquire the lock or raise ``TimeoutError`` with owner diagnostics."""
        if self._fd is not None:
            raise RuntimeError("Codex config lock instance is already acquired")

        pid = self._claim_process_ownership()
        fd: int | None = None
        acquired = False
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            no_follow = getattr(os, "O_NOFOLLOW", None)
            if no_follow is None:  # pragma: no cover - supported on POSIX cook hosts
                raise OSError(
                    errno.ENOTSUP,
                    "Codex config lock requires no-follow open semantics",
                    self.lock_path,
                )
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | no_follow
            fd = os.open(self.lock_path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(
                    errno.EINVAL,
                    "Codex config lock sidecar is not a regular file",
                    self.lock_path,
                )
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        owner = self._owner_diagnostics(fd)
                        raise TimeoutError(
                            "timed out acquiring Codex config lock "
                            f"for {self.config_path} after {self.timeout:.3f}s; "
                            f"lock_path={self.lock_path}; owner={owner}"
                        ) from exc
                    time.sleep(min(self.poll_interval, remaining))

            self._write_owner_diagnostics(fd, pid)
            self._fd = fd
            self._owner_pid = pid
            return self
        except BaseException:
            if fd is not None:
                try:
                    if acquired:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
                finally:
                    try:
                        os.close(fd)
                    finally:
                        self._release_process_ownership(pid)
            else:
                self._release_process_ownership(pid)
            raise

    def release(self) -> None:
        """Release an acquired lock."""
        fd = self._fd
        if fd is None:
            return
        owner_pid = self._owner_pid
        current_pid = os.getpid()
        if owner_pid != current_pid:
            raise RuntimeError(
                "Codex config lock may only be released by its acquiring process; "
                f"owner={owner_pid}, current={current_pid}"
            )
        self._fd = None
        self._owner_pid = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(fd)
            finally:
                self._release_process_ownership(current_pid)

    def __enter__(self) -> CodexConfigLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


__all__ = ["CodexConfigLock"]
