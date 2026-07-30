"""Tests for isolated shell-runner process-group ownership."""

from __future__ import annotations

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


def test_group_liveness_treats_permission_error_as_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(_pgid: int, _signum: int) -> None:
        raise PermissionError

    monkeypatch.setattr(capture_process.os, "killpg", deny)
    assert capture_process._process_group_exists(123)


def test_group_identity_rejects_unsafe_values() -> None:
    with pytest.raises(OwnedProcessError, match="unsafe"):
        capture_process._process_group_exists(1)
