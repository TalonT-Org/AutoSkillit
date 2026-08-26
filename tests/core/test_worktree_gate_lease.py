"""Cross-process ownership tests for the worktree test-gate lease."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from autoskillit.core import WorktreeGateContention, WorktreeGateLease
from tests.conftest import production_interpreter_env

pytestmark = [
    pytest.mark.layer("core"),
    pytest.mark.medium,
    pytest.mark.skipif(os.name != "posix", reason="worktree gate leases require POSIX flock"),
]


def _start_descriptor_holder(fd: int) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                "os.setsid()\n"
                "os.fstat(int(sys.argv[1]))\n"
                "sys.stdout.write('ready\\n')\n"
                "sys.stdout.flush()\n"
                "sys.stdin.buffer.read(1)\n"
            ),
            str(fd),
        ],
        env=production_interpreter_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(fd,),
        text=True,
    )
    assert child.stdout is not None
    readable, _, _ = select.select([child.stdout], [], [], 5)
    assert readable, "gate holder did not confirm inherited descriptor"
    assert child.stdout.readline() == "ready\n"
    return child


def _kill_holder_tree(child: subprocess.Popen[str]) -> None:
    """Kill only the original child process group, guarding against PID reuse."""
    if child.poll() is not None:
        return

    process = psutil.Process(child.pid)
    expected_create_time = process.create_time()
    try:
        if process.is_running() and process.create_time() == expected_create_time:
            os.killpg(child.pid, signal.SIGKILL)
    except psutil.NoSuchProcess:
        pass
    child.wait(timeout=5)


def _close_lease(lease: WorktreeGateLease, diagnostic_path: Path) -> None:
    lease.close()
    diagnostic_path.unlink(missing_ok=True)


def test_orphaned_inherited_gate_lease_blocks_new_acquisition(tmp_path: Path) -> None:
    """An inherited descriptor, not a stale sidecar, remains the lease authority."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    lease = WorktreeGateLease.acquire(worktree, invocation_id="parent-gate")
    diagnostic_path = lease.diagnostic_path
    child: subprocess.Popen[str] | None = None
    try:
        child = _start_descriptor_holder(lease.inherited_fds[0])
        lease.close()

        with pytest.raises(WorktreeGateContention) as exc_info:
            WorktreeGateLease.acquire(worktree, invocation_id="contender-gate")

        message = str(exc_info.value)
        assert os.path.realpath(worktree) in message
        assert str(os.getpid()) in message
    finally:
        if child is not None:
            _kill_holder_tree(child)
        _close_lease(lease, diagnostic_path)


def test_gate_lease_releases_when_holder_tree_dies(tmp_path: Path) -> None:
    """The next holder acquires after every inherited descriptor has died."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    lease = WorktreeGateLease.acquire(worktree, invocation_id="parent-gate")
    diagnostic_path = lease.diagnostic_path
    child: subprocess.Popen[str] | None = None
    successor: WorktreeGateLease | None = None
    try:
        child = _start_descriptor_holder(lease.inherited_fds[0])
        lease.close()
        _kill_holder_tree(child)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                successor = WorktreeGateLease.acquire(worktree, invocation_id="successor-gate")
            except WorktreeGateContention:
                time.sleep(0.02)
            else:
                break
        assert successor is not None, "gate lease remained held after its holder tree died"
        successor_diagnostic = successor.diagnostic_path.read_text()
        assert "successor-gate" in successor_diagnostic
        assert "parent-gate" not in successor_diagnostic
    finally:
        if successor is not None:
            _close_lease(successor, successor.diagnostic_path)
        if child is not None:
            _kill_holder_tree(child)
        _close_lease(lease, diagnostic_path)
        diagnostic_path.unlink(missing_ok=True)


@pytest.mark.parametrize("path_kind", ["canonical", "trailing", "relative", "symlink"])
def test_worktree_gate_lease_normalizes_cwd_at_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_kind: str,
) -> None:
    """The owner canonicalizes every path form rather than trusting its callers."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    symlink = tmp_path / "worktree-link"
    symlink.symlink_to(worktree, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    candidate = {
        "canonical": str(worktree),
        "trailing": f"{worktree}{os.sep}",
        "relative": "worktree",
        "symlink": str(symlink),
    }[path_kind]

    lease = WorktreeGateLease.acquire(candidate, invocation_id=f"{path_kind}-owner")
    diagnostic_path = lease.diagnostic_path
    try:
        with pytest.raises(WorktreeGateContention):
            WorktreeGateLease.acquire(worktree, invocation_id=f"{path_kind}-contender")
    finally:
        _close_lease(lease, diagnostic_path)
