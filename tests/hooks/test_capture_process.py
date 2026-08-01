"""Tests for isolated shell-runner process-group ownership."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

import autoskillit.hooks._capture_process as capture_process
from autoskillit.hooks._capture_process import (
    OwnedProcessError,
    OwnedProcessGroup,
    spawn_owned_process,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class _OrderedProcess:
    def __init__(self, events: list[str], *, poll_error: bool = False) -> None:
        self.pid = 4321
        self.returncode: int | None = None
        self._events = events
        self._poll_error = poll_error

    def poll(self) -> int:
        self._events.append("poll")
        if self._poll_error:
            raise RuntimeError("injected poll failure")
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._events.append("wait")
        self.returncode = 0
        return 0


class _HeldPipe:
    def fileno(self) -> int:
        return 99


class _DrainProcess(_OrderedProcess):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.stdout = _HeldPipe()


class _EmptySelector:
    def __init__(self) -> None:
        self.closed = False

    def register(self, _descriptor: int, _events: int) -> None:
        return

    def select(self, _timeout: float) -> list[tuple[object, int]]:
        return []

    def close(self) -> None:
        self.closed = True


def _record_group_settlement(
    owner: OwnedProcessGroup,
    events: list[str],
) -> None:
    del owner
    events.append("settle_group")


def test_wait_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    assert owner.wait() == 0
    assert events == ["poll", "settle_group", "wait"]


def test_settle_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    assert owner.settle() == 0
    assert events == ["poll", "settle_group", "wait"]


def test_settle_error_path_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast(
        "subprocess.Popen[bytes]",
        _OrderedProcess(events, poll_error=True),
    )
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    with pytest.raises(RuntimeError, match="injected poll failure"):
        owner.settle()
    assert events == ["poll", "settle_group", "wait"]


def test_remaining_group_gets_bounded_term_grace_before_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    signals: list[signal.Signals] = []
    waits: list[tuple[int, float]] = []
    outcomes = iter((False, True))

    monkeypatch.setattr(
        capture_process,
        "_process_group_has_live_members",
        lambda _pgid: True,
    )

    def wait_for_settlement(pgid: int, timeout: float) -> bool:
        waits.append((pgid, timeout))
        return next(outcomes)

    monkeypatch.setattr(
        capture_process,
        "_wait_for_remaining_group_settlement",
        wait_for_settlement,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum: signals.append(signum),
    )

    owner._settle_remaining_group()

    assert waits == [
        (owner.pgid, capture_process._TERM_TIMEOUT_SECONDS),
        (owner.pgid, capture_process._KILL_TIMEOUT_SECONDS),
    ]
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode is None


def test_remaining_group_exiting_during_term_grace_is_not_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    signals: list[signal.Signals] = []

    monkeypatch.setattr(
        capture_process,
        "_process_group_has_live_members",
        lambda _pgid: True,
    )
    monkeypatch.setattr(
        capture_process,
        "_wait_for_remaining_group_settlement",
        lambda _pgid, timeout: timeout == capture_process._TERM_TIMEOUT_SECONDS,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum: signals.append(signum),
    )

    owner._settle_remaining_group()

    assert signals == [signal.SIGTERM]
    assert process.returncode is None


def test_wait_cancellation_still_settles_and_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)

    def cancel_wait(
        _process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float | None,
    ) -> bool:
        del timeout_seconds
        events.append("cancel")
        raise KeyboardInterrupt

    monkeypatch.setattr(
        capture_process,
        "_wait_for_leader_exit_without_reaping",
        cancel_wait,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    with pytest.raises(KeyboardInterrupt):
        owner.wait()

    assert events == ["cancel", "settle_group", "wait"]
    assert process.returncode == 0
    assert owner._restored


def test_signal_handlers_forward_every_terminal_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    previous = {signum: object() for signum in capture_process._FORWARDED_SIGNALS}
    installed: dict[signal.Signals, object] = {}
    forwarded: list[signal.Signals] = []

    monkeypatch.setattr(
        capture_process.signal,
        "getsignal",
        lambda signum: previous[signum],
    )
    monkeypatch.setattr(
        capture_process.signal,
        "signal",
        lambda signum, handler: installed.__setitem__(signum, handler),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum: forwarded.append(signum),
    )

    assert capture_process._install_signal_forwarding(owner) == previous
    for signum in capture_process._FORWARDED_SIGNALS:
        handler = installed[signum]
        assert callable(handler)
        handler(signum, None)

    assert forwarded == list(capture_process._FORWARDED_SIGNALS)


def test_descendant_held_pipe_has_bounded_term_kill_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _DrainProcess(events))
    owner = OwnedProcessGroup(process=process, pgid=process.pid)
    selector = _EmptySelector()
    signals: list[signal.Signals] = []
    monotonic_values = iter((0.0, 0.3, 0.9))

    monkeypatch.setattr(capture_process.os, "set_blocking", lambda *_args: None)
    monkeypatch.setattr(
        capture_process.selectors,
        "DefaultSelector",
        lambda: selector,
    )
    monkeypatch.setattr(
        capture_process.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum: signals.append(signum),
    )

    result = capture_process._drain_capture(
        owner,
        -1,
        64,
        digest_factory=hashlib.sha256,
        write_all=lambda *_args: None,
    )

    assert result.truncated
    assert result.measurement.total_bytes == 0
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert selector.closed
    assert process.returncode is None


def test_pty_foreground_handoff_and_parent_state_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd, terminal_fd = os.openpty()
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    previous_pgid = 2468
    foreground_changes: list[tuple[int, int]] = []
    previous_handlers = {signum: object() for signum in capture_process._FORWARDED_SIGNALS}
    restored_handlers: list[tuple[signal.Signals, object]] = []

    class TerminalInput:
        def fileno(self) -> int:
            return terminal_fd

    monkeypatch.setattr(capture_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(
        capture_process.os,
        "tcgetpgrp",
        lambda descriptor: previous_pgid if descriptor == terminal_fd else -1,
    )
    monkeypatch.setattr(
        capture_process,
        "_safe_tcsetpgrp",
        lambda descriptor, pgid: foreground_changes.append((descriptor, pgid)),
    )
    monkeypatch.setattr(
        capture_process,
        "_install_signal_forwarding",
        lambda _owner: previous_handlers,
    )
    monkeypatch.setattr(
        capture_process.signal,
        "signal",
        lambda signum, handler: restored_handlers.append((signum, handler)),
    )

    try:
        owner = capture_process.adopt_owned_process(
            process,
            inherit_terminal=True,
        )
        owner._restore_parent_state()
        owner._restore_parent_state()
    finally:
        os.close(master_fd)
        os.close(terminal_fd)

    assert foreground_changes == [
        (terminal_fd, process.pid),
        (terminal_fd, previous_pgid),
    ]
    assert restored_handlers == list(previous_handlers.items())


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_owned_process_natural_exit_is_reaped(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        owner = spawn_owned_process(
            [sys.executable, "-c", "raise SystemExit(7)"],
            cwd_fd=cwd_fd,
            env=os.environ,
            capture_output=True,
        )
        assert owner.pgid == owner.pid
        assert owner.wait() == 7
        with pytest.raises(ProcessLookupError):
            os.killpg(owner.pgid, 0)
    finally:
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_poll_observes_exit_without_reaping_group_leader(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        [sys.executable, "-c", "raise SystemExit(0)"],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        deadline = time.monotonic() + 3
        while owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)

        assert owner.poll() == 0
        assert owner.returncode is None
        os.killpg(owner.pgid, 0)
        assert owner.wait() == 0
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_owned_process_escalates_term_ignoring_leader(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        owner = spawn_owned_process(
            [
                sys.executable,
                "-c",
                (
                    "import signal,time;"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                    "print('ready', flush=True);"
                    "time.sleep(30)"
                ),
            ],
            cwd_fd=cwd_fd,
            env=os.environ,
            capture_output=True,
        )
        assert owner.stdout is not None
        assert owner.stdout.readline() == b"ready\n"
        assert owner.settle() == -signal.SIGKILL
        with pytest.raises(ProcessLookupError):
            os.killpg(owner.pgid, 0)
    finally:
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_settle_removes_same_group_child_and_grandchild(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    child_code = """
import signal
import subprocess
import sys
import time

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

def stop(_signum, _frame):
    grandchild.wait(timeout=2)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(str(grandchild.pid))
time.sleep(30)
"""
    parent_code = """
import signal
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[2]])
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(str(child.pid))
deadline = time.monotonic() + 3
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)

def stop(_signum, _frame):
    child.wait(timeout=2)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print("ready", flush=True)
time.sleep(30)
"""
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        [
            sys.executable,
            "-c",
            parent_code,
            str(child_pid_path),
            str(grandchild_pid_path),
            child_code,
        ],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline() == b"ready\n"
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))

        assert owner.settle() == 0

        with pytest.raises(ProcessLookupError):
            os.killpg(owner.pgid, 0)
        for descendant_pid in (child_pid, grandchild_pid):
            with pytest.raises(ProcessLookupError):
                os.kill(descendant_pid, 0)
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


def test_group_liveness_treats_permission_error_as_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(_pgid: int, _signum: int) -> None:
        raise PermissionError

    monkeypatch.setattr(capture_process.os, "killpg", deny)
    assert capture_process._process_group_exists(123)


def test_permission_limited_group_liveness_is_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(_pgid: int, _signum: int) -> None:
        raise PermissionError

    monkeypatch.setattr(capture_process.os, "killpg", deny)
    monkeypatch.setattr(
        capture_process.os,
        "scandir",
        lambda _path: (_ for _ in ()).throw(PermissionError),
    )

    assert capture_process._process_group_has_live_members(123) is None


def test_group_identity_rejects_unsafe_values() -> None:
    with pytest.raises(OwnedProcessError, match="unsafe"):
        capture_process._process_group_exists(1)
