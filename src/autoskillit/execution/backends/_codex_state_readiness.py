"""Guarded read-only readiness probing for Codex state storage."""

from __future__ import annotations

import math
import sqlite3
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.execution.backends._readiness import ObserverStatus

_CODEX_STATE_READINESS_COMMIT = "ad65f016ed0c91992fb175fa881a373cc460dd2a"


@dataclass(frozen=True, slots=True)
class _StateReadinessDef:
    database_name: str
    upstream_commit: str


_SUPPORTED_STATE_CONTRACTS = {
    "codex-cli 0.145.0": _StateReadinessDef(
        database_name="state_5.sqlite",
        upstream_commit=_CODEX_STATE_READINESS_COMMIT,
    )
}


@dataclass(frozen=True, slots=True)
class CodexStateReadinessProbe:
    """Read the version-mapped disposable Codex state database without mutation."""

    codex_version: str
    sqlite_home: Path
    poll_interval_seconds: float = 0.05
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be finite and positive")
        object.__setattr__(self, "sqlite_home", Path(self.sqlite_home))

    @property
    def database_path(self) -> Path | None:
        """Return the exact database path for a supported Codex version."""
        compatibility = _SUPPORTED_STATE_CONTRACTS.get(self.codex_version)
        return None if compatibility is None else self.sqlite_home / compatibility.database_name

    @property
    def upstream_commit(self) -> str | None:
        """Return the source revision defining the probed schema contract."""
        compatibility = _SUPPORTED_STATE_CONTRACTS.get(self.codex_version)
        return None if compatibility is None else compatibility.upstream_commit

    def check(self) -> ObserverStatus:
        """Perform one zero-wait, read-only readiness observation."""
        database_path = self.database_path
        if database_path is None:
            return ObserverStatus.UNSUPPORTED_VERSION
        try:
            path_stat = database_path.lstat()
        except FileNotFoundError:
            return ObserverStatus.ABSENT
        except OSError:
            return ObserverStatus.CORRUPT
        if not stat.S_ISREG(path_stat.st_mode):
            return ObserverStatus.CORRUPT

        connection: sqlite3.Connection | None = None
        try:
            uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=0.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 0")
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(backfill_state)")
                if len(row) > 1 and isinstance(row[1], str)
            }
            if not {"id", "status"}.issubset(columns):
                return ObserverStatus.SCHEMA_CHANGED
            row = connection.execute("SELECT status FROM backfill_state WHERE id = 1").fetchone()
            if row is None or len(row) != 1 or not isinstance(row[0], str):
                return ObserverStatus.INCOMPLETE
            return ObserverStatus.READY if row[0] == "complete" else ObserverStatus.INCOMPLETE
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                return ObserverStatus.LOCKED
            if "no such table" in message or "no such column" in message:
                return ObserverStatus.SCHEMA_CHANGED
            return ObserverStatus.CORRUPT
        except (OSError, sqlite3.DatabaseError, ValueError):
            return ObserverStatus.CORRUPT
        finally:
            if connection is not None:
                connection.close()

    def wait(
        self,
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ObserverStatus:
        """Poll until ready, a terminal adapter failure, timeout, or cancellation."""
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        is_cancelled = cancelled or (lambda: False)
        deadline = self._clock() + timeout_seconds
        while True:
            if is_cancelled():
                return ObserverStatus.CANCELLED
            if self._clock() >= deadline:
                return ObserverStatus.TIMEOUT
            status = self.check()
            if status is ObserverStatus.READY:
                return status
            if status in {
                ObserverStatus.CORRUPT,
                ObserverStatus.SCHEMA_CHANGED,
                ObserverStatus.UNSUPPORTED_VERSION,
            }:
                return status
            remaining = deadline - self._clock()
            if remaining <= 0:
                return ObserverStatus.TIMEOUT
            self._sleep(min(self.poll_interval_seconds, remaining))


__all__ = ["CodexStateReadinessProbe"]
