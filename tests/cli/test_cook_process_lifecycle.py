"""Cook-attempt process ownership and callback ordering contracts."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.cli.session._session_process import run_cook_attempt
from autoskillit.cli.session.pty._observer import PtyObserver
from autoskillit.core import CmdSpec, ValidatedAddDir
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_launcher_fd_merge_preserves_first_seen_lease_priority() -> None:
    from autoskillit.cli.session._session_process import _merge_launcher_fds

    assert _merge_launcher_fds((11, 7, 11, 5), 7) == (11, 7, 5)
    assert _merge_launcher_fds((11, 7), 13) == (11, 7, 13)


def test_pty_launcher_argv_preserves_first_seen_lease_priority() -> None:
    from autoskillit.cli.session.pty._exec import launcher_argv

    master_fd, slave_fd = os.openpty()
    first_read, first_write = os.pipe()
    second_read, second_write = os.pipe()
    try:
        argv = launcher_argv(
            slave_fd,
            ("agent",),
            lease_fds=(second_write, first_write, second_write),
        )
    finally:
        for fd in (
            master_fd,
            slave_fd,
            first_read,
            first_write,
            second_read,
            second_write,
        ):
            os.close(fd)

    separator = argv.index("--")
    assert argv[4:separator] == (str(second_write), str(first_write))


def _spec(tmp_path: Path, code: str, *, env: dict[str, str] | None = None) -> CmdSpec:
    return CmdSpec(
        cmd=(sys.executable, "-c", code),
        env=dict(os.environ) if env is None else env,
        cwd=str(tmp_path.resolve()),
    )


def _wait_until_gone(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _kill_if_alive(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _assert_unsupported_platform(tmp_path: Path) -> bool:
    if os.name == "posix":
        return False
    with pytest.raises(RuntimeError, match="POSIX process-group ownership"):
        run_cook_attempt(
            _spec(tmp_path, "pass"),
            pass_fds=(),
            on_spawn=lambda _pid, _pgid: None,
            on_reaped=lambda _pid, _pgid: None,
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )
    return True


def test_direct_attempt_owns_new_group_and_reaps_before_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if _assert_unsupported_platform(tmp_path):
        return

    actual_popen = subprocess.Popen
    popen_kwargs: dict[str, object] = {}

    def recording_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return actual_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    events: list[tuple[str, int, int]] = []

    def on_spawn(pid: int, pgid: int) -> None:
        assert os.getpgid(pid) == pgid
        events.append(("spawn", pid, pgid))

    def on_reaped(pid: int, pgid: int) -> None:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        events.append(("reaped", pid, pgid))

    result = run_cook_attempt(
        _spec(tmp_path, "pass"),
        pass_fds=(),
        on_spawn=on_spawn,
        on_reaped=on_reaped,
        trace=Mock(),
        observer=None,
        not_after=time.time() + 60,
    )

    assert popen_kwargs["cwd"] == str(tmp_path.resolve())
    assert popen_kwargs["start_new_session"] is False
    assert popen_kwargs["process_group"] == 0
    assert popen_kwargs["pass_fds"] == ()
    assert result.pid == result.pgid
    assert result.returncode == 0
    assert events == [
        ("spawn", result.pid, result.pgid),
        ("reaped", result.pid, result.pgid),
    ]


def test_pass_fds_are_inherited_and_callback_identity_is_stable(tmp_path: Path) -> None:
    if _assert_unsupported_platform(tmp_path):
        return
    read_fd, write_fd = os.pipe()
    events: list[tuple[str, int, int]] = []
    try:
        result = run_cook_attempt(
            _spec(
                tmp_path,
                "import os; os.write(int(os.environ['LEASE_FD']), b'owned')",
                env={**os.environ, "LEASE_FD": str(write_fd)},
            ),
            pass_fds=(write_fd,),
            on_spawn=lambda pid, pgid: events.append(("spawn", pid, pgid)),
            on_reaped=lambda pid, pgid: events.append(("reaped", pid, pgid)),
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )
    finally:
        os.close(write_fd)
    try:
        assert os.read(read_fd, 5) == b"owned"
    finally:
        os.close(read_fd)

    assert events == [
        ("spawn", result.pid, result.pgid),
        ("reaped", result.pid, result.pgid),
    ]


def test_grandchild_cannot_outlive_group_empty_reaped_proof(tmp_path: Path) -> None:
    if _assert_unsupported_platform(tmp_path):
        return
    grandchild_path = tmp_path / "grandchild.pid"
    code = (
        "import pathlib, subprocess, sys;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        f"pathlib.Path({str(grandchild_path)!r}).write_text(str(p.pid))"
    )
    grandchild_pid = 0
    reaped_observations: list[bool] = []
    try:
        result = run_cook_attempt(
            _spec(tmp_path, code),
            pass_fds=(),
            on_spawn=lambda _pid, _pgid: None,
            on_reaped=lambda _pid, _pgid: reaped_observations.append(
                _wait_until_gone(int(grandchild_path.read_text()), timeout=1.0)
            ),
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )
        grandchild_pid = int(grandchild_path.read_text())
        assert result.returncode == 0
        assert reaped_observations == [True]
    finally:
        if grandchild_pid:
            _kill_if_alive(grandchild_pid)
            _wait_until_gone(grandchild_pid)


def test_spawn_failure_has_no_callbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if _assert_unsupported_platform(tmp_path):
        return

    def fail_spawn(*_args, **_kwargs):
        raise OSError("synthetic Popen failure")

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    events: list[str] = []

    with pytest.raises(OSError, match="synthetic Popen failure"):
        run_cook_attempt(
            _spec(tmp_path, "pass"),
            pass_fds=(),
            on_spawn=lambda _pid, _pgid: events.append("spawn"),
            on_reaped=lambda _pid, _pgid: events.append("reaped"),
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )

    assert events == []


def test_callback_failure_still_terminates_and_reaps_child(tmp_path: Path) -> None:
    if _assert_unsupported_platform(tmp_path):
        return
    identity: list[tuple[int, int]] = []

    def fail_after_spawn(pid: int, pgid: int) -> None:
        identity.append((pid, pgid))
        raise RuntimeError("trace/storage callback failed")

    with pytest.raises(RuntimeError, match="trace/storage callback failed"):
        run_cook_attempt(
            _spec(tmp_path, "import time; time.sleep(30)"),
            pass_fds=(),
            on_spawn=fail_after_spawn,
            on_reaped=lambda pid, pgid: identity.append((pid, pgid)),
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )

    assert len(identity) == 2
    assert identity[0] == identity[1]
    assert _wait_until_gone(identity[0][0])


def test_pty_attempt_retains_lease_fd_and_owns_controlling_slave(
    tmp_path: Path,
) -> None:
    if _assert_unsupported_platform(tmp_path):
        return
    read_fd, write_fd = os.pipe()
    code = (
        "import os;"
        "assert os.getsid(0) == os.getpid();"
        "assert os.tcgetpgrp(0) == os.getpgrp();"
        "os.write(int(os.environ['LEASE_FD']), b'pty-owned')"
    )
    try:
        result = run_cook_attempt(
            _spec(
                tmp_path,
                code,
                env={**os.environ, "LEASE_FD": str(write_fd)},
            ),
            pass_fds=(write_fd,),
            on_spawn=lambda _pid, _pgid: None,
            on_reaped=lambda _pid, _pgid: None,
            trace=Mock(),
            observer=PtyObserver(readiness_probe=None),
            not_after=time.time() + 60,
        )
    finally:
        os.close(write_fd)
    try:
        assert os.read(read_fd, 9) == b"pty-owned"
    finally:
        os.close(read_fd)
    assert result.pid == result.pgid
    assert result.returncode == 0


def test_cook_attempt_enforces_ceiling_in_both_wait_branches(tmp_path: Path) -> None:
    """A live spawner terminates its child at not_after in both wait branches.

    Closes the "kill leg never exercised" gap: the non-PTY branch's
    _wait_for_owned_exit poll loop and the PTY branch's observer.relay
    cancelled-callback both merely stop *waiting* at the ceiling — the
    actual kill happens in run_cook_attempt's unconditional finally-block
    settle(). This proves both branches reach that settle() promptly
    against a child that never exits on its own.
    """
    if _assert_unsupported_platform(tmp_path):
        return
    ceiling_seconds = 1.5
    sleeper_code = "import time; time.sleep(30)"

    start = time.monotonic()
    result = run_cook_attempt(
        _spec(tmp_path, sleeper_code),
        pass_fds=(),
        on_spawn=lambda _pid, _pgid: None,
        on_reaped=lambda _pid, _pgid: None,
        trace=Mock(),
        observer=None,
        not_after=time.time() + ceiling_seconds,
    )
    elapsed = time.monotonic() - start
    assert _wait_until_gone(result.pid)
    # The ceiling must actually bind — well under the child's own 30s sleep.
    assert elapsed < 15.0

    start = time.monotonic()
    result = run_cook_attempt(
        _spec(tmp_path, sleeper_code),
        pass_fds=(),
        on_spawn=lambda _pid, _pgid: None,
        on_reaped=lambda _pid, _pgid: None,
        trace=Mock(),
        observer=PtyObserver(readiness_probe=None),
        not_after=time.time() + ceiling_seconds,
    )
    elapsed = time.monotonic() - start
    assert _wait_until_gone(result.pid)
    assert elapsed < 15.0


def test_successful_popen_records_spawn_without_post_spawn_pgid_lookup() -> None:
    source = Path(run_cook_attempt.__code__.co_filename).read_text(encoding="utf-8")
    body = source[
        source.index("def run_cook_attempt(") : source.index(
            "\ndef _require_posix_process_ownership"
        )
    ]
    assert "os.getpgid" not in body
    assert body.index("on_spawn(pid, pgid)") < body.index("trace.record_spawn()")


def test_posix_job_control_foreground_handoff_continues_before_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.session import _session_process

    foreground_changes: list[tuple[int, int]] = []
    signals: list[tuple[int, signal.Signals]] = []

    class TerminalInput:
        def fileno(self) -> int:
            return 7

    monkeypatch.setattr(_session_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(_session_process.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(_session_process.os, "tcgetpgrp", lambda _fd: 100)
    monkeypatch.setattr(
        _session_process,
        "_safe_tcsetpgrp",
        lambda fd, pgid: foreground_changes.append((fd, pgid)),
    )
    monkeypatch.setattr(
        _session_process.os,
        "killpg",
        lambda pgid, signum: signals.append((pgid, signum)),
    )

    with _session_process._foreground_process_group(200):
        assert foreground_changes == [(7, 200)]
        assert signals == [(200, signal.SIGCONT)]

    assert foreground_changes == [(7, 200), (7, 100)]


def test_posix_job_control_foreground_handoff_restores_after_continue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.session import _session_process

    foreground_changes: list[tuple[int, int]] = []

    class TerminalInput:
        def fileno(self) -> int:
            return 7

    monkeypatch.setattr(_session_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(_session_process.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(_session_process.os, "tcgetpgrp", lambda _fd: 100)
    monkeypatch.setattr(
        _session_process,
        "_safe_tcsetpgrp",
        lambda fd, pgid: foreground_changes.append((fd, pgid)),
    )
    monkeypatch.setattr(
        _session_process.os,
        "killpg",
        lambda _pgid, _signum: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(PermissionError, match="denied"):
        with _session_process._foreground_process_group(200):
            pytest.fail("caller body must not run after continuation failure")

    assert foreground_changes == [(7, 200), (7, 100)]


def test_posix_job_control_foreground_handoff_annotates_primary_on_restore_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.session import _session_process

    foreground_changes: list[tuple[int, int]] = []

    class TerminalInput:
        def fileno(self) -> int:
            return 7

    monkeypatch.setattr(_session_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(_session_process.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(_session_process.os, "tcgetpgrp", lambda _fd: 100)

    def fake_tcsetpgrp(fd: int, pgid: int) -> None:
        foreground_changes.append((fd, pgid))
        if foreground_changes[-1] == (7, 100):
            raise OSError("restore failed")

    monkeypatch.setattr(_session_process, "_safe_tcsetpgrp", fake_tcsetpgrp)
    monkeypatch.setattr(
        _session_process.os,
        "killpg",
        lambda _pgid, _signum: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(PermissionError) as raised:
        with _session_process._foreground_process_group(200):
            pytest.fail("caller body must not run after continuation failure")

    assert raised.value is not None
    assert any("foreground process-group restoration" in note for note in raised.value.__notes__)
    assert foreground_changes == [(7, 200), (7, 100)]


def test_posix_job_control_foreground_handoff_propagates_restore_failure_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.session import _session_process

    foreground_changes: list[tuple[int, int]] = []

    class TerminalInput:
        def fileno(self) -> int:
            return 7

    monkeypatch.setattr(_session_process.sys, "stdin", TerminalInput())
    monkeypatch.setattr(_session_process.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(_session_process.os, "tcgetpgrp", lambda _fd: 100)

    def fake_tcsetpgrp(fd: int, pgid: int) -> None:
        foreground_changes.append((fd, pgid))
        if foreground_changes[-1] == (7, 100):
            raise OSError("restore failed")

    monkeypatch.setattr(_session_process, "_safe_tcsetpgrp", fake_tcsetpgrp)

    with pytest.raises(OSError, match="restore failed"):
        with _session_process._foreground_process_group(200):
            assert foreground_changes == [(7, 200)]

    assert foreground_changes == [(7, 200), (7, 100)]


def test_managed_pre_spawn_check_rejects_before_process_or_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.session import _session_process

    if _assert_unsupported_platform(tmp_path):
        return
    spawn = Mock()
    monkeypatch.setattr(_session_process, "spawn_owned_process", spawn)
    callbacks = Mock()
    managed_catalog = ValidatedAddDir(path=str(tmp_path / "add-dir"), session_home=str(tmp_path))

    with pytest.raises(RuntimeError, match="catalog changed"):
        run_cook_attempt(
            CmdSpec(
                cmd=(sys.executable, "-c", "pass"),
                env=dict(os.environ),
                cwd=str(tmp_path.resolve()),
                managed_skill_catalog=managed_catalog,
            ),
            pass_fds=(),
            on_spawn=callbacks.spawn,
            on_reaped=callbacks.reaped,
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
            pre_spawn_check=lambda: (_ for _ in ()).throw(RuntimeError("catalog changed")),
        )

    spawn.assert_not_called()
    callbacks.spawn.assert_not_called()
    callbacks.reaped.assert_not_called()


def test_pty_pre_spawn_check_closes_descriptors_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.session import _session_process

    if _assert_unsupported_platform(tmp_path):
        return
    openpty = os.openpty
    opened: list[int] = []

    def tracked_openpty() -> tuple[int, int]:
        master_fd, slave_fd = openpty()
        opened.extend((master_fd, slave_fd))
        return master_fd, slave_fd

    monkeypatch.setattr(_session_process.os, "openpty", tracked_openpty)
    spawn = Mock()
    monkeypatch.setattr(_session_process, "spawn_owned_process", spawn)
    observer = PtyObserver(readiness_probe=None)
    closed_masters: list[int] = []
    close_master = PtyObserver.close_master

    def record_close_master(self: PtyObserver, master_fd: int) -> None:
        closed_masters.append(master_fd)
        close_master(self, master_fd)

    monkeypatch.setattr(PtyObserver, "close_master", record_close_master)
    callbacks = Mock()
    managed_catalog = ValidatedAddDir(path=str(tmp_path / "add-dir"), session_home=str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="catalog changed"):
            run_cook_attempt(
                CmdSpec(
                    cmd=(sys.executable, "-c", "pass"),
                    env=dict(os.environ),
                    cwd=str(tmp_path.resolve()),
                    managed_skill_catalog=managed_catalog,
                ),
                pass_fds=(),
                on_spawn=callbacks.spawn,
                on_reaped=callbacks.reaped,
                trace=Mock(),
                observer=observer,
                not_after=time.time() + 60,
                pre_spawn_check=lambda: (_ for _ in ()).throw(RuntimeError("catalog changed")),
            )
        assert len(opened) == 2
        assert closed_masters == [opened[0]]
        with pytest.raises(OSError):
            os.fstat(opened[0])
        with pytest.raises(OSError):
            os.fstat(opened[1])
        spawn.assert_not_called()
        callbacks.spawn.assert_not_called()
        callbacks.reaped.assert_not_called()
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize("route", (None, "projected"))
def test_pre_spawn_check_is_only_required_for_managed_codex(
    tmp_path: Path,
    route: str | None,
) -> None:
    from autoskillit.execution.backends._codex_discovery import CODEX_PROJECTED_HOME_ROUTE

    if _assert_unsupported_platform(tmp_path):
        return
    result = run_cook_attempt(
        CmdSpec(
            cmd=(sys.executable, "-c", "pass"),
            env=dict(os.environ),
            cwd=str(tmp_path.resolve()),
            skill_discovery_route=CODEX_PROJECTED_HOME_ROUTE if route else None,
        ),
        pass_fds=(),
        on_spawn=lambda _pid, _pgid: None,
        on_reaped=lambda _pid, _pgid: None,
        trace=Mock(),
        observer=None,
        not_after=time.time() + 60,
    )

    assert result.returncode == 0


def test_managed_launch_requires_a_retained_pre_spawn_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.session import _session_process

    if _assert_unsupported_platform(tmp_path):
        return
    spawn = Mock()
    monkeypatch.setattr(_session_process, "spawn_owned_process", spawn)
    managed_catalog = ValidatedAddDir(path=str(tmp_path / "add-dir"), session_home=str(tmp_path))

    with pytest.raises(RuntimeError, match="requires a pre-spawn check"):
        run_cook_attempt(
            CmdSpec(
                cmd=(sys.executable, "-c", "pass"),
                env=dict(os.environ),
                cwd=str(tmp_path.resolve()),
                managed_skill_catalog=managed_catalog,
            ),
            pass_fds=(),
            on_spawn=lambda _pid, _pgid: None,
            on_reaped=lambda _pid, _pgid: None,
            trace=Mock(),
            observer=None,
            not_after=time.time() + 60,
        )

    spawn.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc process states")
def test_posix_job_control_direct_cook_resumes_stopped_group(tmp_path: Path) -> None:
    from tests.cli._blackbox_launch import _acquire_controlling_terminal

    control_read, control_write = os.pipe()
    master_fd, slave_fd = os.openpty()
    helper: subprocess.Popen[bytes] | None = None
    frames: list[dict[str, object]] = []
    buffer = bytearray()
    child_pgid: int | None = None
    slave_open = True
    helper_code = r"""
import json
import os
import sys
import time
from types import SimpleNamespace

from autoskillit.cli.session._session_process import run_cook_attempt
from autoskillit.core import CmdSpec

control_fd = int(os.environ["CONTROL_FD"])

def report(payload):
    os.write(control_fd, (json.dumps(payload) + "\n").encode())

def process_state(pid):
    tail = open(f"/proc/{pid}/stat", encoding="utf-8").read().rsplit(")", 1)[1]
    return tail.split()[0]

stop_observed = False
reaped = False

def on_spawn(pid, pgid):
    global stop_observed
    report({"kind": "identity", "pid": pid, "pgid": pgid})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if process_state(pid) == "T":
            stop_observed = True
            return
        time.sleep(0.01)
    raise RuntimeError("child never reached stopped terminal state")

def on_reaped(_pid, _pgid):
    global reaped
    reaped = True

try:
    result = run_cook_attempt(
        CmdSpec(
            cmd=(sys.executable, "-c", "import tty; tty.setraw(0)"),
            env=dict(os.environ),
            cwd=os.getcwd(),
        ),
        pass_fds=(control_fd,),
        on_spawn=on_spawn,
        on_reaped=on_reaped,
        trace=SimpleNamespace(record_spawn=lambda: None),
        observer=None,
        not_after=time.time() + 3,
    )
    report(
        {
            "kind": "result",
            "returncode": result.returncode,
            "stop_observed": stop_observed,
            "reaped": reaped,
            "foreground_pgid": os.tcgetpgrp(0),
        }
    )
except BaseException as exc:
    report({"kind": "error", "error": repr(exc), "stop_observed": stop_observed})
    raise
"""
    try:
        helper = subprocess.Popen(
            [sys.executable, "-c", helper_code],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            pass_fds=(control_write,),
            close_fds=True,
            preexec_fn=_acquire_controlling_terminal,
            cwd=tmp_path,
            env={**production_interpreter_env(), "CONTROL_FD": str(control_write)},
        )
        os.close(slave_fd)
        slave_open = False
        deadline = time.monotonic() + 5
        while len(frames) < 2 and time.monotonic() < deadline:
            readable, _, _ = select.select([control_read], [], [], deadline - time.monotonic())
            if not readable:
                break
            chunk = os.read(control_read, 4096)
            if not chunk:
                break
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                frames.append(json.loads(line))
        identity = next((frame for frame in frames if frame["kind"] == "identity"), None)
        assert identity is not None, frames
        child_pgid = int(identity["pgid"])
        result = next((frame for frame in frames if frame["kind"] == "result"), None)
        assert result is not None, frames
        assert result["stop_observed"] is True
        assert result["returncode"] == 0
        assert result["reaped"] is True
        assert result["foreground_pgid"] == helper.pid
        assert helper.wait(timeout=2) == 0
    finally:
        if child_pgid is not None:
            try:
                os.killpg(child_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if helper is not None and helper.poll() is None:
            try:
                os.killpg(helper.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            helper.wait(timeout=2)
        if slave_open:
            os.close(slave_fd)
        os.close(master_fd)
        os.close(control_read)
        os.close(control_write)
