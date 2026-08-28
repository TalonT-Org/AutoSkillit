"""Private bounded file lease for interactive Codex session storage."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import acquire_flock_with_timeout
from autoskillit.execution.backends._codex_fs_atomic import (
    _lexists,
    _require_real_directory,
)

_LOCK_OWNER_DIAGNOSTIC_LIMIT = 4096


@dataclass(slots=True)
class _FileLease:
    path: Path
    fd: int = field(init=False)

    @classmethod
    def acquire(cls, lock_path: Path, *, timeout: float) -> _FileLease:
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
        try:
            if not stat.S_ISREG(os.fstat(instance.fd).st_mode):
                raise RuntimeError(f"Lock path is not a regular file: {lock_path}")
            try:
                acquire_flock_with_timeout(
                    instance.fd,
                    operation=fcntl.LOCK_EX,
                    timeout=timeout,
                    path=lock_path,
                )
            except TimeoutError as exc:
                try:
                    owner_payload = os.pread(instance.fd, _LOCK_OWNER_DIAGNOSTIC_LIMIT, 0)
                except OSError as read_exc:
                    diagnostics = f"unavailable ({type(read_exc).__name__}: {read_exc})"
                else:
                    diagnostics = owner_payload.decode("utf-8", errors="replace") or "unavailable"
                raise TimeoutError(
                    "timed out acquiring Codex session lease "
                    f"for {lock_path} after {timeout:.3f}s; owner={diagnostics}"
                ) from exc
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
        os.close(fd)
