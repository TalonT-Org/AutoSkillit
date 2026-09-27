"""Tests for isolated shell-runner process-group ownership."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import autoskillit.hooks._capture_process as capture_process
import autoskillit.hooks._capture_spawn as capture_spawn
from autoskillit.hooks._capture_process import (
    OwnedProcessError,
    OwnedProcessGroup,
    spawn_owned_process,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def test_capture_process_reexports_spawn_implementation() -> None:
    assert capture_process.__all__ == [
        "OwnedProcessError",
        "OwnedProcessGroup",
        "SignalOrigin",
        "spawn_owned_process",
    ]
    for name in (
        "spawn_owned_process",
        "_finish_owned_spawn",
        "_scrubbed_user_environment",
        "_spawn_bash",
        "_TRUSTED_BASH_CANDIDATES",
    ):
        assert getattr(capture_process, name) is getattr(capture_spawn, name)


@pytest.mark.parametrize(
    "first_import",
    (
        "_capture_process",
        "autoskillit.hooks._capture_process",
        "_capture_spawn",
        "autoskillit.hooks._capture_spawn",
    ),
)
def test_process_and_spawn_import_orders_share_module_authority(
    tmp_path: Path,
    first_import: str,
) -> None:
    src_dir = Path(__file__).parents[2] / "src"
    hooks_dir = src_dir / "autoskillit" / "hooks"
    site_packages = sysconfig.get_paths()["purelib"]
    code = r"""
import importlib
import sys

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
sys.path.append(sys.argv[3])
importlib.import_module(sys.argv[4])

package_process = importlib.import_module("autoskillit.hooks._capture_process")
bare_process = importlib.import_module("_capture_process")
package_spawn = importlib.import_module("autoskillit.hooks._capture_spawn")
bare_spawn = importlib.import_module("_capture_spawn")

assert package_process is bare_process
assert package_spawn is bare_spawn
assert package_spawn._capture_process is package_process
assert package_process.OwnedProcessGroup is bare_process.OwnedProcessGroup
assert package_process._OWNED_PROCESS_SPAWN_TOKEN is bare_process._OWNED_PROCESS_SPAWN_TOKEN
for name in (
    "spawn_owned_process",
    "_finish_owned_spawn",
    "_scrubbed_user_environment",
    "_spawn_bash",
    "_TRUSTED_BASH_CANDIDATES",
):
    assert getattr(package_process, name) is getattr(package_spawn, name)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            code,
            str(src_dir),
            str(hooks_dir),
            site_packages,
            first_import,
        ],
        env=production_interpreter_env(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


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


def _fake_anchor(pid: int) -> SimpleNamespace:
    return SimpleNamespace(pid=pid, returncode=None)


def test_arbitrary_handle_cannot_be_adopted_as_owned_group() -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))

    with pytest.raises(TypeError, match="spawn helper"):
        OwnedProcessGroup(
            process=process,
            pgid=process.pid,
            anchor=_fake_anchor(process.pid),
            _lifeline_fd=-1,
        )


def test_wait_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_release_lifeline",
        lambda current: (
            events.append("release_lifeline"),
            setattr(current.anchor, "returncode", 0),
        ),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    assert owner.wait() == 0
    assert events == ["poll", "settle_group", "release_lifeline", "wait"]


def test_settle_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_release_lifeline",
        lambda current: (
            events.append("release_lifeline"),
            setattr(current.anchor, "returncode", 0),
        ),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    assert owner.settle() == 0
    assert events == ["poll", "settle_group", "release_lifeline", "wait"]


def test_settle_error_path_settles_owned_group_before_reaping_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast(
        "subprocess.Popen[bytes]",
        _OrderedProcess(events, poll_error=True),
    )
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_settle_remaining_group",
        lambda current: _record_group_settlement(current, events),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_release_lifeline",
        lambda current: (
            events.append("release_lifeline"),
            setattr(current.anchor, "returncode", 0),
        ),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    with pytest.raises(RuntimeError, match="injected poll failure"):
        owner.settle()
    assert events == ["poll", "settle_group", "release_lifeline", "wait"]


def test_remaining_group_gets_bounded_term_grace_excluding_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    signals: list[tuple[signal.Signals, object]] = []
    waits: list[tuple[int, float, int | None]] = []

    monkeypatch.setattr(
        capture_process,
        "_process_group_has_live_members",
        lambda _pgid, *, ignore_pid=None: True,
    )

    def wait_for_settlement(
        pgid: int,
        timeout: float,
        *,
        ignore_pid: int | None = None,
    ) -> bool:
        waits.append((pgid, timeout, ignore_pid))
        return False

    monkeypatch.setattr(
        capture_process,
        "_wait_for_remaining_group_settlement",
        wait_for_settlement,
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum, *, origin: signals.append((signum, origin)),
    )

    owner._settle_remaining_group()

    assert waits == [(owner.pgid, capture_process._TERM_TIMEOUT_SECONDS, owner.anchor_pid)]
    assert signals == [(signal.SIGTERM, capture_process.SignalOrigin.RUNNER)]
    assert process.returncode is None


def test_remaining_group_exiting_during_term_grace_is_not_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    signals: list[tuple[signal.Signals, object]] = []

    monkeypatch.setattr(
        capture_process,
        "_process_group_has_live_members",
        lambda _pgid, *, ignore_pid=None: True,
    )
    monkeypatch.setattr(
        capture_process,
        "_wait_for_remaining_group_settlement",
        lambda _pgid, timeout, *, ignore_pid=None: (
            timeout == capture_process._TERM_TIMEOUT_SECONDS
        ),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum, *, origin: signals.append((signum, origin)),
    )

    owner._settle_remaining_group()

    assert signals == [(signal.SIGTERM, capture_process.SignalOrigin.RUNNER)]
    assert process.returncode is None


def test_wait_cancellation_still_settles_and_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = cast("subprocess.Popen[bytes]", _OrderedProcess(events))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )

    def cancel_wait(
        _process: subprocess.Popen[bytes],
        _pgid: int,
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
    monkeypatch.setattr(
        OwnedProcessGroup,
        "_release_lifeline",
        lambda current: (
            events.append("release_lifeline"),
            setattr(current.anchor, "returncode", 0),
        ),
    )
    monkeypatch.setattr(capture_process, "_wait_for_group_exit", lambda *_args: True)

    with pytest.raises(KeyboardInterrupt):
        owner.wait()

    assert events == ["cancel", "settle_group", "release_lifeline", "wait"]
    assert process.returncode == 0
    assert owner._restored


def test_signal_handlers_forward_every_terminal_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    previous = {signum: object() for signum in capture_process._FORWARDED_SIGNALS}
    installed: dict[signal.Signals, object] = {}
    forwarded: list[tuple[signal.Signals, object]] = []

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
        lambda _owner, signum, *, origin: forwarded.append((signum, origin)),
    )

    assert capture_process._install_signal_forwarding(owner) == previous
    for signum in capture_process._FORWARDED_SIGNALS:
        handler = installed[signum]
        assert callable(handler)
        handler(signum, None)

    assert forwarded == [
        (signum, capture_process.SignalOrigin.FORWARDED)
        for signum in capture_process._FORWARDED_SIGNALS
    ]


def test_pty_foreground_handoff_and_parent_state_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd, terminal_fd = os.openpty()
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    previous_pgid = 2468
    foreground_changes: list[tuple[int, int]] = []
    signals: list[tuple[int, signal.Signals]] = []
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
        capture_process.os,
        "killpg",
        lambda pgid, signum: signals.append((pgid, signum)),
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
        owner = capture_process.OwnedProcessGroup(
            process=process,
            pgid=process.pid,
            anchor=_fake_anchor(process.pid),
            _lifeline_fd=-1,
            _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
        )
        owner._previous_handlers = previous_handlers
        terminal = capture_process._take_foreground_process_group(process.pid)
        assert terminal is not None
        owner._terminal_fd, owner._previous_foreground_pgid = terminal
        owner._restore_parent_state()
        owner._restore_parent_state()
    finally:
        os.close(master_fd)
        os.close(terminal_fd)

    assert foreground_changes == [
        (terminal_fd, process.pid),
        (terminal_fd, previous_pgid),
    ]
    assert signals == [(process.pid, signal.SIGCONT)]
    assert restored_handlers == list(previous_handlers.items())


def test_posix_job_control_hook_handoff_restores_after_continue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd, terminal_fd = os.openpty()
    foreground_changes: list[tuple[int, int]] = []
    previous_pgid = 2468

    class TerminalInput:
        def fileno(self) -> int:
            return terminal_fd

    monkeypatch.setattr(capture_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(capture_process.os, "tcgetpgrp", lambda _fd: previous_pgid)
    monkeypatch.setattr(
        capture_process,
        "_safe_tcsetpgrp",
        lambda fd, pgid: foreground_changes.append((fd, pgid)),
    )
    monkeypatch.setattr(
        capture_process.os,
        "killpg",
        lambda _pgid, _signum: (_ for _ in ()).throw(PermissionError("denied")),
    )

    try:
        with pytest.raises(PermissionError, match="denied"):
            capture_process._take_foreground_process_group(1234)
    finally:
        os.close(master_fd)
        os.close(terminal_fd)

    assert foreground_changes == [(terminal_fd, 1234), (terminal_fd, previous_pgid)]


def test_posix_job_control_hook_handoff_annotates_primary_on_restore_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd, terminal_fd = os.openpty()
    foreground_changes: list[tuple[int, int]] = []
    previous_pgid = 2468

    class TerminalInput:
        def fileno(self) -> int:
            return terminal_fd

    monkeypatch.setattr(capture_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(capture_process.os, "tcgetpgrp", lambda _fd: previous_pgid)

    def fake_tcsetpgrp(fd: int, pgid: int) -> None:
        foreground_changes.append((fd, pgid))
        if foreground_changes[-1] == (terminal_fd, previous_pgid):
            raise OSError("restore failed")

    monkeypatch.setattr(capture_process, "_safe_tcsetpgrp", fake_tcsetpgrp)
    monkeypatch.setattr(
        capture_process.os,
        "killpg",
        lambda _pgid, _signum: (_ for _ in ()).throw(PermissionError("denied")),
    )

    try:
        with pytest.raises(PermissionError) as raised:
            capture_process._take_foreground_process_group(1234)
    finally:
        os.close(master_fd)
        os.close(terminal_fd)

    assert raised.value is not None
    assert any("foreground process-group restoration" in note for note in raised.value.__notes__)
    assert foreground_changes == [
        (terminal_fd, 1234),
        (terminal_fd, previous_pgid),
    ]


@pytest.mark.skipif(
    not all(hasattr(capture_process.os, name) for name in ("WSTOPPED", "CLD_STOPPED")),
    reason="stopped non-reaping observation is required",
)
def test_posix_job_control_hook_poll_reports_stopped_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=production_interpreter_env(),
    )
    pgid = process.pid + 9999

    class StoppedStatus:
        si_code = capture_process.os.CLD_STOPPED
        si_status = signal.SIGTTIN

    captured: list[tuple[object, int, int]] = []

    def fake_waitid(idtype: object, pid: int, flags: int) -> StoppedStatus:
        captured.append((idtype, pid, flags))
        return StoppedStatus()

    monkeypatch.setattr(capture_process.os, "waitid", fake_waitid)
    try:
        with pytest.raises(OwnedProcessError) as raised:
            capture_process._poll_leader_without_reaping(process, pgid)
    finally:
        process.kill()
        process.wait()

    assert raised.value.leader_pid == process.pid
    assert raised.value.pgid == pgid
    assert raised.value.stop_signal == signal.SIGTTIN
    assert captured, "waitid must be invoked"
    _, observed_pid, observed_flags = captured[0]
    assert observed_pid == process.pid
    assert observed_flags & capture_process.os.WSTOPPED, "waitid must request stopped observation"


@pytest.mark.skipif(
    not all(hasattr(capture_process.os, name) for name in ("WSTOPPED", "CLD_STOPPED")),
    reason="stopped non-reaping observation is required",
)
def test_posix_job_control_hook_poll_skips_stopped_when_include_stopped_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=production_interpreter_env(),
    )
    pgid = process.pid

    class StoppedStatus:
        si_code = capture_process.os.CLD_STOPPED
        si_status = signal.SIGTTIN

    captured: list[int] = []

    def fake_waitid(idtype: object, pid: int, flags: int) -> None:
        del idtype, pid
        captured.append(flags)
        return None

    monkeypatch.setattr(capture_process.os, "waitid", fake_waitid)
    try:
        assert (
            capture_process._poll_leader_without_reaping(process, pgid, include_stopped=False)
            is None
        )
    finally:
        process.kill()
        process.wait()

    assert captured, "waitid must be invoked"
    observed_flags = captured[0]
    assert not (observed_flags & capture_process.os.WSTOPPED), (
        "waitid must not request stopped observation when include_stopped is False"
    )


@pytest.mark.parametrize("capture_output", (False, True), ids=("direct", "capture"))
@pytest.mark.parametrize("use_bash", (False, True), ids=("argv", "bash"))
def test_spawn_owned_process_stdin_matches_capture_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capture_output: bool,
    use_bash: bool,
) -> None:
    popen_kwargs: list[dict[str, object]] = []
    process = SimpleNamespace(pid=4242, returncode=None)

    def fake_popen(*_args: object, **kwargs: object) -> object:
        popen_kwargs.append(kwargs)
        stdout = kwargs.get("stdout")
        if len(popen_kwargs) == 1:
            assert isinstance(stdout, int)
            os.write(stdout, b"\n")
        return process

    monkeypatch.setattr(capture_spawn.subprocess, "Popen", fake_popen)

    def finish_spawn(*_args: object, **kwargs: object) -> object:
        os.close(cast("int", kwargs["lifeline_fd"]))
        return object()

    monkeypatch.setattr(capture_spawn, "_finish_owned_spawn", finish_spawn)

    if use_bash:
        capture_spawn._spawn_bash("/bin/bash", "exit 0", capture_output=capture_output)
    else:
        cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            capture_spawn.spawn_owned_process(
                ["command"], cwd_fd=cwd_fd, env={}, capture_output=capture_output
            )
        finally:
            os.close(cwd_fd)

    assert len(popen_kwargs) == 2
    anchor_kwargs, leader_kwargs = popen_kwargs
    assert isinstance(anchor_kwargs["stdin"], int)
    assert isinstance(anchor_kwargs["stdout"], int)
    assert anchor_kwargs["stderr"] == subprocess.DEVNULL
    assert anchor_kwargs["process_group"] == 0
    assert anchor_kwargs["env"] == {}
    assert anchor_kwargs["cwd"] == "/"
    assert leader_kwargs["process_group"] == 4242
    assert leader_kwargs["start_new_session"] is False
    assert leader_kwargs["stdin"] is (subprocess.DEVNULL if capture_output else None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_owned_process_natural_exit_is_reaped(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner: OwnedProcessGroup | None = None
    try:
        owner = spawn_owned_process(
            [sys.executable, "-c", "raise SystemExit(7)"],
            cwd_fd=cwd_fd,
            env=os.environ,
            capture_output=True,
        )
        assert type(owner) is capture_process.OwnedProcessGroup
        assert owner._spawn_token is capture_process._OWNED_PROCESS_SPAWN_TOKEN
        assert owner.pgid == owner.anchor_pid != owner.pid
        assert owner.wait() == 7
        assert not capture_process._process_group_exists(owner.pgid)
    finally:
        if owner is not None and owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/status").exists(),
    reason="lifeline anchor probes require procfs",
)
def test_owned_group_is_anchored_by_lifeline_process(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        ["/bin/bash", "-c", "sleep 30"],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        assert owner.pgid == owner.anchor_pid
        assert owner.anchor_pid != owner.pid
        assert os.getpgid(owner.pid) == owner.pgid
        assert os.readlink(f"/proc/{owner.anchor_pid}/fd/0").startswith("pipe:[")

        status = Path(f"/proc/{owner.anchor_pid}/status").read_text(encoding="utf-8")
        ignored = int(
            next(line.split()[1] for line in status.splitlines() if line.startswith("SigIgn:")), 16
        )
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            assert ignored & (1 << (int(signum) - 1))
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_lifeline_release_is_the_group_kill(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        ["/bin/bash", "-c", "(trap '' TERM; sleep 30) & sleep 30"],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        owner._release_lifeline()

        assert owner.anchor.returncode is not None
        assert capture_process._process_group_has_live_members(owner.pgid) is False
        assert owner._runner_signals == []
        assert owner.wait() == -signal.SIGKILL
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_settlement_ignores_anchor_liveness(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(["true"], cwd_fd=cwd_fd, env=os.environ, capture_output=True)
    try:
        started = time.monotonic()
        assert owner.wait() == 0
        assert time.monotonic() - started < 1.0
        assert capture_process._process_group_has_live_members(owner.pgid) is not True
        assert owner.anchor.returncode is not None
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_post_eof_settlement_terminates_then_releases_lifeline(tmp_path: Path) -> None:
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        ["/bin/bash", "-c", "(trap '' TERM; sleep 30) >/dev/null 2>&1 & exit 0"],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        started = time.monotonic()
        assert owner.wait() == 0
        assert time.monotonic() - started < capture_process._TERM_TIMEOUT_SECONDS + 1.5
        assert capture_process._process_group_has_live_members(owner.pgid) is False
        assert owner.anchor.returncode is not None
    finally:
        if owner.returncode is None:
            owner.settle()
        os.close(cwd_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_forwarded_signals_do_not_disarm_lifeline(tmp_path: Path) -> None:
    origin = capture_process.SignalOrigin
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    owner = spawn_owned_process(
        ["/bin/bash", "-c", "trap '' INT TERM HUP QUIT; sleep 30"],
        cwd_fd=cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            owner.signal_group(signum, origin=origin.FORWARDED)
            assert owner.anchor.poll() is None
        assert owner.runner_signalled is False
    finally:
        if owner.returncode is None:
            owner.settle()

    second_cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    self_signalling = spawn_owned_process(
        [
            "/bin/bash",
            "-c",
            "trap '' USR1 USR2 ALRM; kill -USR1 0; kill -USR2 0; kill -ALRM 0; sleep 30",
        ],
        cwd_fd=second_cwd_fd,
        env=os.environ,
        capture_output=True,
    )
    try:
        time.sleep(0.2)
        assert self_signalling.anchor.poll() is None
        assert self_signalling.runner_signalled is False
    finally:
        if self_signalling.returncode is None:
            self_signalling.settle()
        os.close(second_cwd_fd)
        os.close(cwd_fd)


def test_signal_group_requires_origin_and_records_runner_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(capture_process.os, "getpgid", lambda _pid: owner.pgid)
    monkeypatch.setattr(capture_process, "_process_group_exists", lambda _pgid: True)
    monkeypatch.setattr(
        capture_process.os,
        "killpg",
        lambda pgid, signum: sent.append((pgid, signum)),
    )

    with pytest.raises(TypeError):
        owner.signal_group(signal.SIGTERM)

    owner.signal_group(signal.SIGTERM, origin=capture_process.SignalOrigin.RUNNER)
    owner.terminate()
    owner.kill()

    assert sent == [
        (owner.pgid, signal.SIGTERM),
        (owner.pgid, signal.SIGTERM),
        (owner.pgid, signal.SIGKILL),
    ]
    assert owner.runner_signalled


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_anchor_that_dies_before_arming_fails_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture_spawn, "_ANCHOR_SCRIPT", "exit 3")
    real_popen = capture_spawn.subprocess.Popen
    anchors: list[subprocess.Popen[bytes]] = []
    leader_calls: list[object] = []

    def record_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        if kwargs.get("process_group") == 0:
            anchors.append(process)
        return process

    monkeypatch.setattr(capture_spawn.subprocess, "Popen", record_popen)
    monkeypatch.setattr(
        capture_spawn,
        "_popen_leader",
        lambda *args, **kwargs: leader_calls.append((args, kwargs)),
    )

    with pytest.raises(Exception, match="cannot spawn capture shell") as raised:
        capture_spawn._spawn_bash("/bin/bash", "exit 0", capture_output=False)

    assert type(raised.value).__name__ == "CaptureSetupError"
    assert leader_calls == []
    assert len(anchors) == 1
    assert anchors[0].returncode is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_leader_spawn_failure_releases_anchor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_spawn_anchor = capture_spawn._spawn_anchor
    anchors: list[subprocess.Popen[bytes]] = []

    def record_anchor(*args: object, **kwargs: object):
        anchor, lifeline_fd = real_spawn_anchor(*args, **kwargs)
        anchors.append(anchor)
        return anchor, lifeline_fd

    monkeypatch.setattr(capture_spawn, "_spawn_anchor", record_anchor)
    monkeypatch.setattr(
        capture_spawn,
        "_popen_leader",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.E2BIG, "injected leader spawn failure")
        ),
    )

    with pytest.raises(Exception, match="argument/environment exceeds system limit") as mapped:
        capture_spawn._spawn_bash("/bin/bash", "exit 0", capture_output=False)

    assert type(mapped.value).__name__ == "CaptureSetupError"
    assert anchors[-1].returncode is not None

    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    original_error = OSError(errno.E2BIG, "injected argv spawn failure")
    monkeypatch.setattr(
        capture_spawn,
        "_popen_leader",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(original_error),
    )
    try:
        with pytest.raises(OSError) as raised:
            spawn_owned_process(["command"], cwd_fd=cwd_fd, env=os.environ, capture_output=False)
        assert raised.value is original_error
        assert anchors[-1].returncode is not None
    finally:
        os.close(cwd_fd)


def test_anchor_spawn_failure_maps_without_abandon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited_cwd_fds: list[int] = []
    closed_fds: list[int] = []
    abandon_calls: list[BaseException] = []
    real_open = capture_spawn.os.open
    real_close = capture_spawn.os.close

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "." and dir_fd is None:
            inherited_cwd_fds.append(fd)
        return fd

    def record_close(fd: int) -> None:
        if fd in inherited_cwd_fds:
            closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(capture_spawn.os, "open", record_open)
    monkeypatch.setattr(capture_spawn.os, "close", record_close)
    monkeypatch.setattr(
        capture_spawn,
        "_spawn_anchor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.EMFILE, "injected anchor spawn failure")
        ),
    )
    monkeypatch.setattr(
        capture_spawn,
        "_abandon_anchor",
        lambda _anchor, _fd, *, primary_error: abandon_calls.append(primary_error),
    )

    with pytest.raises(Exception, match="cannot spawn capture shell") as raised:
        capture_spawn._spawn_bash("/bin/bash", "exit 0", capture_output=False)

    assert type(raised.value).__name__ == "CaptureSetupError"
    assert abandon_calls == []
    assert len(inherited_cwd_fds) == 1
    assert closed_fds == inherited_cwd_fds
    with pytest.raises(OSError):
        os.fstat(inherited_cwd_fds[0])


def test_owned_spawn_original_cwd_open_failure_releases_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = _fake_anchor(4242)
    open_error = OSError(errno.EMFILE, "injected cwd open failure")
    abandoned: list[tuple[object, int, BaseException]] = []
    monkeypatch.setattr(capture_process, "_resolve_bash", lambda: "/bin/bash")
    monkeypatch.setattr(
        capture_spawn,
        "_spawn_anchor",
        lambda *_args, **_kwargs: (anchor, 99),
    )
    monkeypatch.setattr(
        capture_spawn.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(open_error),
    )
    monkeypatch.setattr(
        capture_spawn,
        "_abandon_anchor",
        lambda spawned_anchor, fd, *, primary_error: abandoned.append(
            (spawned_anchor, fd, primary_error)
        ),
    )

    with pytest.raises(OSError) as raised:
        spawn_owned_process([], cwd_fd=10, env={}, capture_output=False)

    assert raised.value is open_error
    assert abandoned == [(anchor, 99, open_error)]


@pytest.mark.parametrize(
    ("anchor_exits", "group_settles", "expected_signals"),
    [
        ((False, True), (True,), [signal.SIGKILL]),
        ((True,), (False, True), [signal.SIGKILL]),
        ((True,), (True,), []),
    ],
    ids=("anchor-needs-kill", "group-needs-kill", "clean-release"),
)
def test_release_lifeline_escalates_only_when_anchor_does_not_exit(
    monkeypatch: pytest.MonkeyPatch,
    anchor_exits: tuple[bool, ...],
    group_settles: tuple[bool, ...],
    expected_signals: list[signal.Signals],
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    anchor = SimpleNamespace(pid=process.pid, returncode=None)

    def reap_anchor(*, timeout: float | None = None) -> int:
        del timeout
        anchor.returncode = 0
        return 0

    anchor.wait = reap_anchor
    lifeline_fd = os.open(os.devnull, os.O_RDONLY)
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=anchor,
        _lifeline_fd=lifeline_fd,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    anchor_outcomes = iter(anchor_exits)
    group_outcomes = iter(group_settles)
    signals: list[signal.Signals] = []
    closed_fds: list[int] = []
    real_close = os.close

    def record_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(
        capture_process,
        "_wait_for_leader_exit_without_reaping",
        lambda *_args, **_kwargs: next(anchor_outcomes),
    )
    monkeypatch.setattr(
        capture_process,
        "_wait_for_remaining_group_settlement",
        lambda *_args, **_kwargs: next(group_outcomes),
    )
    monkeypatch.setattr(
        OwnedProcessGroup,
        "signal_group",
        lambda _owner, signum, *, origin: signals.append(signum),
    )
    monkeypatch.setattr(capture_process.os, "close", record_close)

    owner._release_lifeline()

    assert signals == expected_signals
    assert closed_fds == [lifeline_fd]
    assert owner.anchor.returncode == 0


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/stat").exists(),
    reason="runner-death oracle requires procfs",
)
def test_runner_death_kills_user_group(tmp_path: Path) -> None:
    src_dir = Path(__file__).parents[2] / "src"
    hooks_dir = src_dir / "autoskillit" / "hooks"
    site_packages = sysconfig.get_paths()["purelib"]
    helper_code = r"""
import os
import sys
import time

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
sys.path.append(sys.argv[3])
from autoskillit.hooks._capture_process import spawn_owned_process

cwd_fd = os.open(".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
owner = spawn_owned_process(
    ["/bin/bash", "-c", "echo $$ > leader.pid; exec sleep 30"],
    cwd_fd=cwd_fd,
    env=os.environ,
    capture_output=False,
)
os.close(cwd_fd)
with open("owned-group.pid", "w", encoding="utf-8") as stream:
    stream.write(str(owner.pgid))
open("helper-ready", "w").close()
time.sleep(30)
"""
    helper = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            helper_code,
            str(src_dir),
            str(hooks_dir),
            site_packages,
        ],
        cwd=tmp_path,
        env=production_interpreter_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    leader_pid_path = tmp_path / "leader.pid"
    group_pid_path = tmp_path / "owned-group.pid"
    ready_path = tmp_path / "helper-ready"
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline and not (
            ready_path.exists() and leader_pid_path.exists() and group_pid_path.exists()
        ):
            if helper.poll() is not None:
                break
            time.sleep(0.01)
        assert helper.poll() is None
        assert ready_path.exists()
        leader_pid = int(leader_pid_path.read_text(encoding="utf-8"))
        group_pid = int(group_pid_path.read_text(encoding="utf-8"))

        helper.kill()
        helper.wait(timeout=5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                stat = Path(f"/proc/{leader_pid}/stat").read_text(encoding="utf-8")
            except FileNotFoundError:
                break
            if stat.rpartition(")")[2].lstrip().startswith("Z"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("owned leader remained live after its runner died")
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)
        if group_pid_path.exists():
            group_pid = int(group_pid_path.read_text(encoding="utf-8"))
            try:
                os.killpg(group_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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
        assert capture_process._process_group_exists(owner.pgid)
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
        assert not capture_process._process_group_exists(owner.pgid)
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

        assert not capture_process._process_group_exists(owner.pgid)
        assert owner.anchor.returncode is not None
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


@pytest.mark.parametrize("anchor_poll_fails", (False, True))
def test_owned_spawn_identity_error_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    anchor_poll_fails: bool,
) -> None:
    class FailedIdentityProcess:
        pid = 4321
        returncode = None

        def kill(self) -> None:
            raise RuntimeError("kill cleanup failed")

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise OSError("reap cleanup failed")

    identity_error: BaseException = (
        OwnedProcessError("anchor identity poll failed")
        if anchor_poll_fails
        else OSError("identity unavailable")
    )
    anchor = _fake_anchor(4320)
    lifeline_fd = os.open(os.devnull, os.O_RDONLY)
    abandoned: list[BaseException] = []
    monkeypatch.setattr(
        capture_process.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(identity_error),
    )
    monkeypatch.setattr(
        capture_spawn._capture_process,
        "_poll_leader_without_reaping",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(identity_error) if anchor_poll_fails else None
        ),
    )
    monkeypatch.setattr(
        capture_spawn,
        "_abandon_anchor",
        lambda _anchor, _fd, *, primary_error: abandoned.append(primary_error),
    )

    try:
        with pytest.raises(OwnedProcessError, match="unsafe") as raised:
            capture_spawn._finish_owned_spawn(
                cast("subprocess.Popen[bytes]", FailedIdentityProcess()),
                inherit_terminal=False,
                anchor=anchor,
                lifeline_fd=lifeline_fd,
            )

        assert raised.value.__cause__ is identity_error
        assert any("kill cleanup failed" in note for note in raised.value.__notes__)
        assert any("reap cleanup failed" in note for note in raised.value.__notes__)
        assert abandoned == [raised.value]
    finally:
        os.close(lifeline_fd)


def test_owned_spawn_restore_error_preserves_settlement_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast("subprocess.Popen[bytes]", _OrderedProcess([]))
    owner = OwnedProcessGroup(
        process=process,
        pgid=process.pid,
        anchor=_fake_anchor(process.pid),
        _lifeline_fd=-1,
        _spawn_token=capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    restore_error = OSError("restore failed")
    fchdir_calls = 0
    monkeypatch.setattr(capture_process, "_resolve_bash", lambda: "/bin/bash")

    def fail_restore(_fd: int) -> None:
        nonlocal fchdir_calls
        fchdir_calls += 1
        if fchdir_calls == 2:
            raise restore_error

    monkeypatch.setattr(capture_spawn.os, "open", lambda *_args: 99)
    monkeypatch.setattr(capture_spawn.os, "fchdir", fail_restore)
    monkeypatch.setattr(capture_spawn.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        capture_spawn,
        "_spawn_anchor",
        lambda *_args, **_kwargs: (_fake_anchor(process.pid), 99),
    )
    monkeypatch.setattr(capture_spawn.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(capture_spawn, "_finish_owned_spawn", lambda *_args, **_kwargs: owner)
    monkeypatch.setattr(
        OwnedProcessGroup,
        "settle",
        lambda _self: (_ for _ in ()).throw(RuntimeError("settlement failed")),
    )

    with pytest.raises(OwnedProcessError, match="cannot restore") as raised:
        spawn_owned_process([], cwd_fd=10, env={}, capture_output=False)

    assert raised.value.__cause__ is restore_error
    assert any("settlement failed" in note for note in raised.value.__notes__)
