"""Cook-attempt process ownership and callback ordering contracts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.cli.session._session_process import run_cook_attempt
from autoskillit.cli.session.pty._observer import PtyObserver
from autoskillit.core import CmdSpec

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
        )
    return True


def test_direct_attempt_owns_new_group_and_reaps_before_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if _assert_unsupported_platform(tmp_path):
        return
    import autoskillit.cli.session._session_process as process_mod

    actual_popen = subprocess.Popen
    popen_kwargs: dict[str, object] = {}

    def recording_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return actual_popen(*args, **kwargs)

    monkeypatch.setattr(process_mod.subprocess, "Popen", recording_popen)
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
    import autoskillit.cli.session._session_process as process_mod

    def fail_spawn(*_args, **_kwargs):
        raise OSError("synthetic Popen failure")

    monkeypatch.setattr(process_mod.subprocess, "Popen", fail_spawn)
    events: list[str] = []

    with pytest.raises(OSError, match="synthetic Popen failure"):
        run_cook_attempt(
            _spec(tmp_path, "pass"),
            pass_fds=(),
            on_spawn=lambda _pid, _pgid: events.append("spawn"),
            on_reaped=lambda _pid, _pgid: events.append("reaped"),
            trace=Mock(),
            observer=None,
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
        )
    finally:
        os.close(write_fd)
    try:
        assert os.read(read_fd, 9) == b"pty-owned"
    finally:
        os.close(read_fd)
    assert result.pid == result.pgid
    assert result.returncode == 0


def test_successful_popen_records_spawn_without_post_spawn_pgid_lookup() -> None:
    source = Path(run_cook_attempt.__code__.co_filename).read_text(encoding="utf-8")
    body = source[
        source.index("def run_cook_attempt(") : source.index(
            "\ndef _require_posix_process_ownership"
        )
    ]
    assert "os.getpgid" not in body
    assert body.index("on_spawn(pid, pgid)") < body.index("trace.record_spawn()")
