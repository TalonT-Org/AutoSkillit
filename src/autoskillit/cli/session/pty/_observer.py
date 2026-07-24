"""Byte-transparent PTY observation and Codex startup-readiness probing."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import math
import os
import re
import selectors
import signal
import sqlite3
import stat
import termios
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Any

_WINDOW_LIMIT = 64 * 1024
_RELAY_CHUNK_SIZE = 64 * 1024
_RELAY_SELECT_SECONDS = 0.05
_SUPPORTED_STATE_DATABASES = {"codex-cli 0.145.0": "state_5.sqlite"}
_ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1b
    (?:
        \][^\x07]*(?:\x07|\x1b\\)
        |
        [@-_][0-?]*[ -/]*[@-~]
    )
    """,
    re.VERBOSE,
)
_HOOK_REVIEW_PATTERNS = (
    re.compile(r"\bhooks?\s+(?:need|needs|require|requires)\s+review\b", re.IGNORECASE),
    re.compile(r"\breview\b.{0,80}\bhooks?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:approve|allow)\b.{0,80}\bhooks?\b", re.IGNORECASE | re.DOTALL),
)


class ObserverStatus(StrEnum):
    """Typed outcomes from the guarded Codex state-readiness adapter."""

    READY = "ready"
    ABSENT = "absent"
    LOCKED = "locked"
    CORRUPT = "corrupt"
    INCOMPLETE = "incomplete"
    SCHEMA_CHANGED = "schema_changed"
    UNSUPPORTED_VERSION = "unsupported_version"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


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
        filename = _SUPPORTED_STATE_DATABASES.get(self.codex_version)
        return None if filename is None else self.sqlite_home / filename

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


@dataclass(slots=True)
class PtyObserver:
    """Own PTY I/O observation while leaving process-group policy to its caller."""

    readiness_probe: CodexStateReadinessProbe | None
    on_first_output: Callable[[], None] | None = None
    on_hook_review: Callable[[], None] | None = None
    on_readiness: Callable[[ObserverStatus], None] | None = None
    first_output_seen: bool = field(default=False, init=False)
    hook_review_seen: bool = field(default=False, init=False)
    readiness_status: ObserverStatus | None = field(default=None, init=False)
    retained_output: bytes = field(default=b"", init=False)
    normalized_window: str = field(default="", init=False)
    _closed_fds: set[int] = field(default_factory=set, init=False, repr=False)

    def observe_output(self, chunk: bytes) -> bytes:
        """Observe a master-side chunk and return its original bytes unchanged."""
        if not isinstance(chunk, bytes):
            raise TypeError("PTY output chunks must be bytes")
        if not chunk:
            return chunk
        if not self.first_output_seen:
            self.first_output_seen = True
            if self.on_first_output is not None:
                self.on_first_output()

        self.retained_output = (self.retained_output + chunk)[-_WINDOW_LIMIT:]
        decoded = self.retained_output.decode("utf-8", errors="replace")
        normalized = _ANSI_ESCAPE_RE.sub("", decoded)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized_bytes = normalized.encode("utf-8")[-_WINDOW_LIMIT:]
        self.normalized_window = normalized_bytes.decode("utf-8", errors="ignore")

        if not self.hook_review_seen and any(
            pattern.search(self.normalized_window) for pattern in _HOOK_REVIEW_PATTERNS
        ):
            self.hook_review_seen = True
            if self.on_hook_review is not None:
                self.on_hook_review()
        return chunk

    def check_readiness(self) -> ObserverStatus | None:
        """Observe readiness once and emit each changed status at most once."""
        if self.readiness_probe is None:
            return None
        status = self.readiness_probe.check()
        if status is not self.readiness_status:
            self.readiness_status = status
            if self.on_readiness is not None:
                self.on_readiness(status)
        return status

    def wait_for_readiness(
        self,
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ObserverStatus | None:
        """Delegate a bounded readiness wait and publish its terminal status."""
        if self.readiness_probe is None:
            return None
        status = self.readiness_probe.wait(
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
        if status is not self.readiness_status:
            self.readiness_status = status
            if self.on_readiness is not None:
                self.on_readiness(status)
        return status

    def relay(
        self,
        master_fd: int,
        *,
        stdin_fd: int = 0,
        stdout_fd: int = 1,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        """Relay stdin/master traffic until master EOF, closing the master once."""
        if master_fd < 0 or stdin_fd < 0 or stdout_fd < 0:
            raise ValueError("PTY relay descriptors must be non-negative")
        is_cancelled = cancelled or (lambda: False)
        selector = selectors.DefaultSelector()
        previous_winch: Callable[[int, FrameType | None], Any] | int | None = None
        installed_winch = False

        def copy_window_size() -> None:
            if not os.isatty(stdin_fd):
                return
            try:
                size = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, b"\0" * 8)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
            except OSError:
                return

        def handle_winch(_signum: int, _frame: object) -> None:
            copy_window_size()

        try:
            if os.isatty(stdin_fd):
                tty.setraw(stdin_fd)
                copy_window_size()
                try:
                    previous_winch = signal.getsignal(signal.SIGWINCH)
                    signal.signal(signal.SIGWINCH, handle_winch)  # noqa: TID251
                    installed_winch = True
                except ValueError:
                    previous_winch = None
            selector.register(master_fd, selectors.EVENT_READ, "master")
            try:
                selector.register(stdin_fd, selectors.EVENT_READ, "stdin")
            except OSError:
                pass

            while not is_cancelled():
                self.check_readiness()
                for key, _events in selector.select(_RELAY_SELECT_SECONDS):
                    source_fd = int(key.fd)
                    try:
                        chunk = os.read(source_fd, _RELAY_CHUNK_SIZE)
                    except OSError as exc:
                        if key.data == "master" and exc.errno == errno.EIO:
                            return
                        if exc.errno in {errno.EAGAIN, errno.EINTR}:
                            continue
                        raise
                    if not chunk:
                        if key.data == "master":
                            return
                        with contextlib.suppress(Exception):
                            selector.unregister(stdin_fd)
                        continue
                    if key.data == "master":
                        self._write_all(stdout_fd, self.observe_output(chunk))
                    else:
                        self._write_all(master_fd, chunk)
        finally:
            if installed_winch and previous_winch is not None:
                signal.signal(signal.SIGWINCH, previous_winch)  # noqa: TID251
            selector.close()
            self.close_master(master_fd)

    def close_master(self, master_fd: int) -> None:
        """Close a PTY master descriptor exactly once."""
        if master_fd in self._closed_fds:
            return
        self._closed_fds.add(master_fd)
        os.close(master_fd)

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            try:
                written = os.write(fd, view)
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError("PTY relay write made no progress")
            view = view[written:]


__all__ = ["CodexStateReadinessProbe", "ObserverStatus", "PtyObserver"]
