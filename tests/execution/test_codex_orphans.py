"""Tests for orphaned codex TUI detection and reap."""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
import regex as re

from autoskillit.core import ProcessCleanupResult, read_starttime_ticks
from autoskillit.execution import (
    OrphanedCodexProcess,
    find_orphaned_codex_processes,
    reap_orphaned_codex_processes,
)
from autoskillit.execution.process._codex_orphans import _is_deleted_pty_target

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
]


def _wait_for_fake_codex_ready(pid: int, name: str, stdin_mode: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            fd0_target = os.readlink(f"/proc/{pid}/fd/0")
        except (FileNotFoundError, OSError):
            time.sleep(0.01)
            continue
        target_ready = {
            "orphan_pty": bool(re.fullmatch(r"/dev/pts/\d+ \(deleted\)", fd0_target)),
            "live_pty": bool(re.fullmatch(r"/dev/pts/\d+", fd0_target)),
            "devnull": fd0_target == "/dev/null",
            "deleted_file": fd0_target.endswith(" (deleted)"),
        }[stdin_mode]
        if comm == name and target_ready:
            return
        time.sleep(0.01)
    raise AssertionError(f"fake Codex process {pid} did not reach {stdin_mode} readiness")


@pytest.fixture
def _spawn_fake_codex(tmp_path):
    """Factory fixture spawning a symlinked-Python child with controllable stdin mode."""
    children: list[subprocess.Popen] = []

    def _spawn(*, name: str, stdin_mode: str):
        link = tmp_path / name
        link.symlink_to(sys.executable)

        if stdin_mode in {"orphan_pty", "live_pty"}:
            master_fd, slave_fd = pty.openpty()
            try:
                child = subprocess.Popen(
                    [str(link), "-c", "import time; time.sleep(120)"],
                    stdin=slave_fd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except BaseException:
                os.close(slave_fd)
                os.close(master_fd)
                raise
            os.close(slave_fd)
            if stdin_mode == "orphan_pty":
                os.close(master_fd)  # closes master → child fd 0 becomes deleted pty
            else:
                # keep master_fd open — store it on child for teardown
                child._master_fd = master_fd  # type: ignore[attr-defined]
        elif stdin_mode == "devnull":
            child = subprocess.Popen(
                [str(link), "-c", "import time; time.sleep(120)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif stdin_mode == "deleted_file":
            tmp_file = tmp_path / f"{name}_stdin"
            tmp_file.write_text("x")
            fh = open(tmp_file)
            os.unlink(tmp_file)
            child = subprocess.Popen(
                [str(link), "-c", "import time; time.sleep(120)"],
                stdin=fh,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            fh.close()
        else:
            raise ValueError(f"Unknown stdin_mode: {stdin_mode}")

        children.append(child)
        _wait_for_fake_codex_ready(child.pid, name, stdin_mode)
        return child

    yield _spawn

    for child in children:
        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.kill()
        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.wait(timeout=5)
        # Close any kept master fd
        if hasattr(child, "_master_fd"):
            with contextlib.suppress(OSError):
                os.close(child._master_fd)


def _unique_name(prefix: str) -> str:
    """A ≤15 char process name unique enough to avoid cross-test collisions."""
    return f"{prefix}{os.getpid() % 100000:05d}"


def test_detects_orphan_after_pty_master_close(_spawn_fake_codex):
    name = _unique_name("cdxorph")
    child = _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

    results = find_orphaned_codex_processes(process_name=name)

    assert len(results) == 1
    orphan = results[0]
    assert orphan.pid == child.pid
    assert re.match(r"^/dev/pts/\d+ \(deleted\)$", orphan.fd0_target)
    assert orphan.exe_target is not None
    assert orphan.starttime_ticks is not None
    assert orphan.started_at == pytest.approx(psutil.Process(child.pid).create_time(), abs=1)


def test_ignores_live_pty(_spawn_fake_codex):
    name = _unique_name("cdxlive")
    _spawn_fake_codex(name=name, stdin_mode="live_pty")

    assert find_orphaned_codex_processes(process_name=name) == []


def test_ignores_devnull_stdin(_spawn_fake_codex):
    name = _unique_name("cdxdevn")
    _spawn_fake_codex(name=name, stdin_mode="devnull")

    assert find_orphaned_codex_processes(process_name=name) == []


def test_ignores_deleted_regular_file_stdin(_spawn_fake_codex):
    name = _unique_name("cdxdelf")
    _spawn_fake_codex(name=name, stdin_mode="deleted_file")

    assert find_orphaned_codex_processes(process_name=name) == []


def test_ignores_other_process_names(_spawn_fake_codex):
    name_a = _unique_name("cdxaaa")
    name_b = _unique_name("cdxbbb")
    _spawn_fake_codex(name=name_a, stdin_mode="orphan_pty")

    assert find_orphaned_codex_processes(process_name=name_b) == []


def test_scan_skips_vanished_process_rows(_spawn_fake_codex):
    name = _unique_name("cdxvan")
    child = _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

    with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
        child.kill()
    with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
        child.wait(timeout=5)

    # Should complete without raising, and the vanished process shouldn't appear.
    assert find_orphaned_codex_processes(process_name=name) == []


def test_default_process_name_is_codex():
    assert (
        inspect.signature(find_orphaned_codex_processes).parameters["process_name"].default
        == "codex"
    )


def test_reap_kills_orphan(_spawn_fake_codex):
    name = _unique_name("cdxkill")
    child = _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

    orphans = find_orphaned_codex_processes(process_name=name)
    assert len(orphans) == 1

    results = reap_orphaned_codex_processes(orphans)

    assert len(results) == 1
    result = results[0]
    assert result.action == "terminated"
    assert result.survivor_pids == ()
    assert result.access_denied_pids == ()

    deadline = time.monotonic() + 5
    while psutil.pid_exists(child.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child.pid)


def test_reap_kill_escalation_for_term_ignoring_child(tmp_path):
    name = _unique_name("cdxterm")
    link = tmp_path / name
    link.symlink_to(sys.executable)

    master_fd, slave_fd = pty.openpty()
    child = subprocess.Popen(
        [
            str(link),
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
        ],
        stdin=slave_fd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.close(slave_fd)
    os.close(master_fd)

    try:
        time.sleep(0.1)
        orphans = find_orphaned_codex_processes(process_name=name)
        assert len(orphans) == 1

        results = reap_orphaned_codex_processes(orphans)

        assert len(results) == 1
        assert results[0].action == "terminated"

        deadline = time.monotonic() + 5
        while psutil.pid_exists(child.pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not psutil.pid_exists(child.pid)
    finally:
        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.kill()
        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.wait(timeout=5)


class TestReapSkipsStaleIdentity:
    def test_already_exited_pid(self, _spawn_fake_codex):
        name = _unique_name("cdxexit")
        child = _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

        orphans = find_orphaned_codex_processes(process_name=name)
        assert len(orphans) == 1

        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.kill()
        with contextlib.suppress(ProcessLookupError, ChildProcessError, OSError):
            child.wait(timeout=5)

        results = reap_orphaned_codex_processes(orphans)

        assert len(results) == 1
        assert results[0].action == "skipped"

    def test_nonexistent_pid(self, monkeypatch):
        from unittest.mock import Mock

        import autoskillit.execution.process._codex_orphans as codex_orphans

        orphan = OrphanedCodexProcess(
            pid=999999999,
            fd0_target="/dev/pts/99 (deleted)",
            exe_target=None,
            starttime_ticks=1,
            started_at=0.0,
        )
        logger = Mock()
        monkeypatch.setattr(codex_orphans, "logger", logger)

        results = reap_orphaned_codex_processes([orphan])

        assert len(results) == 1
        assert results[0].action == "skipped"
        logger.info.assert_called_once_with("codex_orphan_reap_skipped", pid=orphan.pid)

    def test_live_child_with_mismatched_fd0(self, _spawn_fake_codex):
        name = _unique_name("cdxmism")
        child = _spawn_fake_codex(name=name, stdin_mode="devnull")

        ticks = read_starttime_ticks(child.pid)
        assert ticks is not None

        orphan = OrphanedCodexProcess(
            pid=child.pid,
            fd0_target="/dev/pts/9999 (deleted)",  # fabricated — real stdin is devnull
            exe_target=None,
            starttime_ticks=ticks,
            started_at=0.0,
        )

        results = reap_orphaned_codex_processes([orphan])

        assert len(results) == 1
        assert results[0].action == "skipped"
        assert psutil.pid_exists(child.pid)


def test_reap_touches_no_filesystem_paths():
    src = Path("src/autoskillit/execution/process/_codex_orphans.py")
    tree = ast.parse(src.read_text())

    forbidden = {
        "chmod",
        "chown",
        "copy",
        "copy2",
        "copyfile",
        "mkdir",
        "move",
        "remove",
        "rename",
        "replace",
        "rmdir",
        "rmtree",
        "symlink_to",
        "touch",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in forbidden, f"forbidden call: {func.id}"
        elif isinstance(func, ast.Attribute):
            assert func.attr not in forbidden, f"forbidden call: {func.attr}"


def test_deleted_pty_predicate_strings():
    assert _is_deleted_pty_target("/dev/pts/3 (deleted)") is True
    assert _is_deleted_pty_target("/dev/pts/ptmx (deleted)") is False
    assert _is_deleted_pty_target("/dev/pts/3") is False
    assert _is_deleted_pty_target("/dev/pts/ (deleted)") is False
    assert _is_deleted_pty_target("/tmp/f (deleted)") is False


def test_scan_is_same_uid_scoped(_spawn_fake_codex, monkeypatch):
    name = _unique_name("cdxuid")
    _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

    real_euid = os.geteuid()
    monkeypatch.setattr(
        "autoskillit.execution.process._codex_orphans.os.geteuid",
        lambda: real_euid + 1,
    )

    assert find_orphaned_codex_processes(process_name=name) == []


def test_reap_reports_incomplete_on_survivors(_spawn_fake_codex, monkeypatch):
    from unittest.mock import Mock

    import autoskillit.execution.process._codex_orphans as codex_orphans

    name = _unique_name("cdxsurv")
    _spawn_fake_codex(name=name, stdin_mode="orphan_pty")

    orphans = find_orphaned_codex_processes(process_name=name)
    assert len(orphans) == 1
    orphan = orphans[0]

    fake = ProcessCleanupResult(root_pid=orphan.pid, survivor_pids=(orphan.pid,))
    logger = Mock()
    monkeypatch.setattr(codex_orphans, "logger", logger)
    monkeypatch.setattr(
        "autoskillit.execution.process._codex_orphans.kill_process_tree",
        lambda pid, **kw: fake,
    )

    results = reap_orphaned_codex_processes(orphans)

    assert len(results) == 1
    assert results[0].action == "incomplete"
    assert results[0].survivor_pids == (orphan.pid,)
    logger.warning.assert_called_once_with(
        "codex_orphan_reap_incomplete",
        pid=orphan.pid,
        survivor_pids=(orphan.pid,),
        access_denied_pids=(),
    )
