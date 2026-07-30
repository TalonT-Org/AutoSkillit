"""Tests for isolated shell-runner process-group ownership."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

import autoskillit.hooks._capture_process as capture_process
from autoskillit.hooks._capture_process import (
    OwnedProcessError,
    spawn_owned_process,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


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
