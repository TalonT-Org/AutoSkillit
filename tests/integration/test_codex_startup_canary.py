"""Opt-in installed-Codex compatibility gate for rollout links and leases."""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.execution.backends.codex import CodexBackend

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported platforms
    fcntl = None  # type: ignore[assignment]

pytestmark = [pytest.mark.large]

_CANARY_ENV = "AUTOSKILLIT_CODEX_STARTUP_CANARY"
_SUPPORTED_VERSION = "codex-cli 0.145.0"
_OUTPUT_CAP = 64 * 1024


def _installed_supported_codex() -> str:
    if os.environ.get(_CANARY_ENV) != "1":
        pytest.skip(f"set {_CANARY_ENV}=1 to run the installed-Codex canary")
    if os.name != "posix" or sys.platform not in {"linux", "darwin"} or fcntl is None:
        pytest.skip("installed-Codex canary requires a supported POSIX PTY/lease platform")
    binary = shutil.which("codex")
    if binary is None:
        pytest.skip("supported installed Codex CLI is not present")
    result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    version = result.stdout.strip()
    if result.returncode != 0 or version != _SUPPORTED_VERSION:
        pytest.skip(f"unsupported installed Codex version: {version or 'unknown'}")
    return binary


def _prepare_home(path: Path) -> None:
    path.mkdir()
    (path / "sessions").mkdir()
    source_auth = Path.home() / ".codex" / "auth.json"
    if source_auth.is_file():
        (path / "auth.json").symlink_to(source_auth)


def _assert_competing_lease_is_blocked(path: Path) -> None:
    assert fcntl is not None
    competitor = os.open(path, os.O_RDWR)
    try:
        with pytest.raises(OSError) as caught:
            fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert caught.value.errno in {errno.EACCES, errno.EAGAIN}
    finally:
        os.close(competitor)


def _assert_lease_released(path: Path) -> None:
    assert fcntl is not None
    competitor = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(competitor)


def _run_with_inherited_lease(
    spec,
    *,
    project: Path,
    lease_path: Path,
) -> tuple[bytes, bytes]:
    assert fcntl is not None
    lease_fd = os.open(lease_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lease_fd, fcntl.LOCK_EX)
    process = subprocess.Popen(
        spec.cmd,
        cwd=project,
        env=spec.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(lease_fd,),
        process_group=0,
        start_new_session=False,
    )
    os.close(lease_fd)  # close-only transfer: LOCK_UN would release the shared OFD lock
    try:
        assert process.poll() is None, "Codex exited before inherited-lease observation"
        _assert_competing_lease_is_blocked(lease_path)
        stdout, stderr = process.communicate(timeout=90)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise
    assert process.returncode == 0, stderr[-_OUTPUT_CAP:].decode(errors="replace")
    _assert_lease_released(lease_path)
    return stdout[-_OUTPUT_CAP:], stderr[-_OUTPUT_CAP:]


def _thread_id(stdout: bytes) -> str:
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if record.get("type") == "thread.started":
            thread_id = record.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    pytest.fail("installed Codex did not emit a thread.started identifier")


def _rollouts(home: Path) -> list[Path]:
    return sorted(
        path
        for path in (home / "sessions").rglob("rollout-*")
        if path.is_file() and path.suffix in {".jsonl", ".zst"}
    )


def _assert_jsonl_schema(path: Path) -> None:
    if path.suffix == ".zst":
        assert path.stat().st_size > 0
        return
    lines = path.read_bytes().splitlines()
    assert lines
    assert all(isinstance(json.loads(line), dict) for line in lines)


def test_installed_codex_preserves_staged_rollout_inode_and_inherited_lease(
    tmp_path: Path,
) -> None:
    binary = _installed_supported_codex()
    project = tmp_path / "project"
    project.mkdir()
    diagnostics = project / ".autoskillit" / "temp" / uuid.uuid4().hex[:16]
    diagnostics.mkdir(parents=True)
    fresh_home = tmp_path / "fresh-home"
    _prepare_home(fresh_home)
    backend = CodexBackend()
    fresh = backend.build_headless_cmd("Respond with exactly: autoskillit startup canary")
    fresh = replace(
        fresh,
        cmd=(binary, *fresh.cmd[1:]),
        env={**fresh.env, "CODEX_HOME": str(fresh_home)},
        cwd=str(project),
    )
    fresh_stdout, fresh_stderr = _run_with_inherited_lease(
        fresh,
        project=project,
        lease_path=tmp_path / "fresh.lease",
    )
    thread_id = _thread_id(fresh_stdout)
    fresh_rollouts = _rollouts(fresh_home)
    assert len(fresh_rollouts) == 1
    _assert_jsonl_schema(fresh_rollouts[0])

    resume_home = tmp_path / "resume-home"
    _prepare_home(resume_home)
    relative_rollout = fresh_rollouts[0].relative_to(fresh_home / "sessions")
    staged = resume_home / "sessions" / relative_rollout
    staged.parent.mkdir(parents=True, exist_ok=True)
    os.link(fresh_rollouts[0], staged)
    staged_identity = (staged.stat().st_dev, staged.stat().st_ino)
    resume = backend.build_resume_cmd(
        resume_session_id=thread_id,
        prompt="Respond with exactly: autoskillit resume canary",
    )
    resume = replace(
        resume,
        cmd=(binary, *resume.cmd[1:]),
        env={**resume.env, "CODEX_HOME": str(resume_home)},
        cwd=str(project),
    )
    resume_stdout, resume_stderr = _run_with_inherited_lease(
        resume,
        project=project,
        lease_path=tmp_path / "resume.lease",
    )
    final_rollouts = _rollouts(resume_home)
    assert len(final_rollouts) == 1
    final = final_rollouts[0]
    _assert_jsonl_schema(final)
    if final.suffix == ".jsonl":
        assert (final.stat().st_dev, final.stat().st_ino) == staged_identity
    else:
        assert not staged.exists()
        assert final.name == f"{staged.name}.zst"

    (diagnostics / "environment.json").write_text(
        json.dumps(
            {
                "codex_version": _SUPPORTED_VERSION,
                "fresh_file_count": len(fresh_rollouts),
                "fresh_allocated_bytes": fresh_rollouts[0].stat().st_blocks * 512,
                "resume_file_count": len(final_rollouts),
                "resume_allocated_bytes": final.stat().st_blocks * 512,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (diagnostics / "fresh.stdout").write_bytes(fresh_stdout)
    (diagnostics / "fresh.stderr").write_bytes(fresh_stderr)
    (diagnostics / "resume.stdout").write_bytes(resume_stdout)
    (diagnostics / "resume.stderr").write_bytes(resume_stderr)
