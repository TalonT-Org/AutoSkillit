"""Byte-transparent PTY observation with injectable startup readiness."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import selectors
import signal
import termios
import tty
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

import regex as re

from autoskillit.core import ObserverStatus, ReadinessProbe

_WINDOW_LIMIT = 64 * 1024
_RELAY_CHUNK_SIZE = 64 * 1024
_RELAY_SELECT_SECONDS = 0.05
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


@dataclass(slots=True)
class PtyObserver:
    """Own PTY I/O observation while leaving process-group policy to its caller."""

    readiness_probe: ReadinessProbe | None
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
        if self.readiness_status is ObserverStatus.READY:
            return ObserverStatus.READY
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
        if self.readiness_status is ObserverStatus.READY:
            return ObserverStatus.READY
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
        selector_factory = selectors.DefaultSelector
        selector = selector_factory()
        previous_winch: Callable[[int, FrameType | None], Any] | int | None = None
        installed_winch = False
        terminal_attributes: list[Any] | None = None

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
                terminal_attributes = termios.tcgetattr(stdin_fd)
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
            try:
                self.check_readiness()
            finally:
                if installed_winch and previous_winch is not None:
                    with contextlib.suppress(ValueError):
                        signal.signal(signal.SIGWINCH, previous_winch)  # noqa: TID251
                if terminal_attributes is not None:
                    with contextlib.suppress(OSError):
                        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, terminal_attributes)
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


__all__ = ["PtyObserver"]
