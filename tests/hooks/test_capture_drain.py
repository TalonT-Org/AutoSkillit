"""Tests for signal-free capture draining and EOF evidence."""

from __future__ import annotations

import hashlib
import importlib
import os
import select
import signal
import time
from pathlib import Path
from types import ModuleType

import pytest

import autoskillit.hooks._capture_process as capture_process
from autoskillit.hooks._capture._authority import CaptureSetupError
from autoskillit.hooks._capture._failure_policy import CaptureFailureReason
from autoskillit.hooks._capture._runner import _write_all
from autoskillit.hooks._capture._snapshot import CaptureMeasurement
from autoskillit.hooks._capture_process import (
    OwnedProcessError,
    OwnedProcessGroup,
    spawn_owned_process,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _drain_module() -> ModuleType:
    """Load the module here so pending implementation work does not break collection."""

    return importlib.import_module("autoskillit.hooks._capture._drain")


def _spawn_shell(tmp_path: Path, command: str) -> tuple[OwnedProcessGroup, int]:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        owner = spawn_owned_process(
            ["bash", "-c", command],
            cwd_fd=cwd_fd,
            env=os.environ,
            capture_output=True,
        )
    except BaseException:
        os.close(cwd_fd)
        raise
    return owner, cwd_fd


def _open_writer(tmp_path: Path) -> int:
    return os.open(tmp_path / "capture.bin", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)


def _finish_owner(owner: OwnedProcessGroup, cwd_fd: int) -> None:
    try:
        owner.settle()
    finally:
        if owner.stdout is not None:
            owner.stdout.close()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_same_group_pipe_holder_after_leader_exit_is_drained_to_eof_without_signals(
    tmp_path: Path,
) -> None:
    owner, cwd_fd = _spawn_shell(tmp_path, "{ sleep 0.3; printf late; } & printf early")
    assert owner.stdout is not None
    started = time.monotonic()
    try:
        with os.fdopen(_open_writer(tmp_path), "wb") as artifact:
            drain = _drain_module()
            evidence = drain.drain_capture(
                owner,
                artifact.fileno(),
                12_000,
                digest_factory=hashlib.sha256,
                write_all=_write_all,
            )

        assert isinstance(evidence, drain.PipeEofEvidence)
        assert evidence.measurement.inline == b"earlylate"
        assert (tmp_path / "capture.bin").read_bytes() == b"earlylate"
        assert owner.runner_signalled is False
        assert time.monotonic() - started >= 0.25
    finally:
        _finish_owner(owner, cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_runner_signal_before_eof_refuses_eof_evidence(tmp_path: Path) -> None:
    owner, cwd_fd = _spawn_shell(tmp_path, "sleep 5 & printf x")
    assert owner.stdout is not None
    try:
        with os.fdopen(_open_writer(tmp_path), "wb") as artifact:
            signal_origin = capture_process.SignalOrigin
            owner.signal_group(signal.SIGTERM, origin=signal_origin.RUNNER)

            with pytest.raises(CaptureSetupError) as raised:
                _drain_module().drain_capture(
                    owner,
                    artifact.fileno(),
                    12_000,
                    digest_factory=hashlib.sha256,
                    write_all=_write_all,
                )

        assert raised.value.reason is CaptureFailureReason.RUNNER_SETTLEMENT
    finally:
        _finish_owner(owner, cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_forwarded_signal_before_eof_still_yields_evidence(tmp_path: Path) -> None:
    owner, cwd_fd = _spawn_shell(tmp_path, "sleep 5 & printf x")
    assert owner.stdout is not None
    pipe_fd = owner.stdout.fileno()
    try:
        with os.fdopen(_open_writer(tmp_path), "wb") as artifact:
            assert select.select([pipe_fd], [], [], 2.0)[0]
            signal_origin = capture_process.SignalOrigin
            owner.signal_group(signal.SIGTERM, origin=signal_origin.FORWARDED)

            drain = _drain_module()
            evidence = drain.drain_capture(
                owner,
                artifact.fileno(),
                12_000,
                digest_factory=hashlib.sha256,
                write_all=_write_all,
            )

        assert isinstance(evidence, drain.PipeEofEvidence)
        assert evidence.measurement.inline == b"x"
        assert owner.runner_signalled is False
    finally:
        _finish_owner(owner, cwd_fd)


def test_pipe_eof_evidence_is_drain_minted_only() -> None:
    with pytest.raises(TypeError):
        _drain_module().PipeEofEvidence(
            measurement=CaptureMeasurement.from_bytes(b"complete", inline_bytes=12_000),
            write_error=None,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_stopped_leader_during_drain_raises(tmp_path: Path) -> None:
    owner, cwd_fd = _spawn_shell(tmp_path, "sleep 5 & kill -STOP $$")
    assert owner.stdout is not None
    try:
        with os.fdopen(_open_writer(tmp_path), "wb") as artifact:
            drain = _drain_module()
            with pytest.raises(OwnedProcessError) as raised:
                drain.drain_capture(
                    owner,
                    artifact.fileno(),
                    12_000,
                    digest_factory=hashlib.sha256,
                    write_all=_write_all,
                )

        assert raised.value.stop_signal == signal.SIGSTOP
    finally:
        _finish_owner(owner, cwd_fd)
