"""Session state persistence for resume support.

IL-1 module: imports from core (IL-0) only. Provides atomic read/write of
dispatch session state files so a dead session can be identified and resumed.
"""

from __future__ import annotations

import fcntl
import json
import os  # noqa: F401 — used at runtime in SessionStateLock
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import atomic_write, get_logger

logger = get_logger(__name__)

_STATE_FILENAME = "dispatch_session_state.json"
_LOCK_FILENAME = "dispatch_session_state.lock"
_FD_CLOSED = -1


@dataclass(frozen=True)
class SessionState:
    session_id: str
    pid: int
    boot_id: str
    starttime_ticks: int
    checkpoint_path: str | None = None
    infra_exit_category: str | None = None

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        d: dict[str, object] = {
            "session_id": self.session_id,
            "pid": self.pid,
            "boot_id": self.boot_id,
            "starttime_ticks": self.starttime_ticks,
        }
        if self.checkpoint_path is not None:
            d["checkpoint_path"] = self.checkpoint_path
        if self.infra_exit_category is not None:
            d["infra_exit_category"] = self.infra_exit_category
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SessionState | None:  # type: ignore[type-arg]
        try:
            return cls(
                session_id=str(data["session_id"]),
                pid=int(data["pid"]),
                boot_id=str(data["boot_id"]),
                starttime_ticks=int(data["starttime_ticks"]),
                checkpoint_path=data.get("checkpoint_path"),
                infra_exit_category=data.get("infra_exit_category"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def persist_session_state(state: SessionState, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _STATE_FILENAME
    atomic_write(path, json.dumps(state.to_dict(), indent=2))
    return path


def read_session_state(state_dir: Path) -> SessionState | None:
    path = state_dir / _STATE_FILENAME
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return SessionState.from_dict(data)


def clear_session_state(state_dir: Path) -> None:
    path = state_dir / _STATE_FILENAME
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("session_state: could not remove state file", path=str(path))


class SessionStateLock:
    """File-based concurrency guard for session resume operations."""

    def __init__(self, state_dir: Path) -> None:
        self._lock_path = state_dir / _LOCK_FILENAME
        self._fd: int = _FD_CLOSED

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self._lock_path), os.O_WRONLY | os.O_CREAT, 0o644)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            if self._fd != _FD_CLOSED:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = _FD_CLOSED
            return False

    def release(self) -> None:
        if self._fd != _FD_CLOSED:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = _FD_CLOSED

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()
