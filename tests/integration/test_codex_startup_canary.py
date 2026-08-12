"""Opt-in installed-Codex compatibility gate for rollout links and leases."""

from __future__ import annotations

import errno
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import zstandard

from autoskillit.execution.backends.codex import CodexBackend
from tests.execution._process_group_helpers import (
    _capture_owned_group_identities,
    _cleanup_owned_process_group,
    _cleanup_process_identities,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported platforms
    fcntl = None  # type: ignore[assignment]

pytestmark = [pytest.mark.large]

_CANARY_ENV = "AUTOSKILLIT_CODEX_STARTUP_CANARY"
_SUPPORTED_VERSION = "codex-cli 0.147.0"
_OUTPUT_CAP = 64 * 1024
_INSTALLED_CODEX_HOME = Path.home() / ".codex"


def _installed_supported_codex() -> str:
    if os.environ.get(_CANARY_ENV) != "1":
        pytest.skip(f"set {_CANARY_ENV}=1 to run the installed-Codex canary")
    if os.name != "posix" or sys.platform not in {"linux", "darwin"} or fcntl is None:
        pytest.skip("installed-Codex canary requires a supported POSIX PTY/lease platform")
    binary = shutil.which("codex")
    if binary is None:
        pytest.fail("installed-Codex canary requested but the Codex CLI is not present")
    result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    version = result.stdout.strip()
    if result.returncode != 0 or version != _SUPPORTED_VERSION:
        pytest.fail(
            "installed-Codex canary requested with unsupported version: "
            f"{version or 'unknown'}; expected {_SUPPORTED_VERSION}"
        )
    return binary


def _prepare_home(path: Path) -> None:
    path.mkdir()
    (path / "sessions").mkdir()
    source_auth = _INSTALLED_CODEX_HOME / "auth.json"
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
    rollout_root: Path,
) -> tuple[bytes, bytes, tuple[int, int], float]:
    assert fcntl is not None
    lease_fd = os.open(lease_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lease_fd, fcntl.LOCK_EX)
    wrapper = """
import json
import os
import sys
import time

command = json.loads(sys.argv[1])
if os.fork() == 0:
    for descriptor in (0, 1, 2):
        try:
            os.close(descriptor)
        except OSError:
            pass
    time.sleep(120)
    os._exit(0)
os.execvpe(command[0], command, os.environ)
"""
    started_at = time.monotonic()
    process = subprocess.Popen(
        (sys.executable, "-c", wrapper, json.dumps(list(spec.cmd))),
        cwd=project,
        env=spec.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(lease_fd,),
        process_group=0,
        start_new_session=False,
    )
    os.close(lease_fd)  # close-only transfer: LOCK_UN would release the shared OFD lock
    owned_identities = _capture_owned_group_identities(process)
    try:
        assert process.poll() is None, "Codex exited before inherited-lease observation"
        _assert_competing_lease_is_blocked(lease_path)
        observed_identity: tuple[int, int] | None = None
        observation_deadline = time.monotonic() + 30
        while observed_identity is None and time.monotonic() < observation_deadline:
            owned_identities.update(_capture_owned_group_identities(process))
            observed = _rollouts_from_root(rollout_root)
            if observed:
                file_stat = observed[0].stat()
                observed_identity = (file_stat.st_dev, file_stat.st_ino)
                break
            if process.poll() is not None:
                break
            time.sleep(0.01)
        owned_identities.update(_capture_owned_group_identities(process))
        if observed_identity is None:
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, stderr[-_OUTPUT_CAP:].decode(errors="replace")
            pytest.fail(
                "Codex produced no live staged rollout inode; "
                f"stdout={stdout[-_OUTPUT_CAP:].decode(errors='replace')!r}; "
                f"stderr={stderr[-_OUTPUT_CAP:].decode(errors='replace')!r}"
            )
        stdout, stderr = process.communicate(timeout=90)
    except BaseException:
        if process.returncode is None:
            _cleanup_owned_process_group(process, timeout=5)
        else:
            _cleanup_process_identities(owned_identities, timeout=5)
        raise
    assert process.returncode == 0, stderr[-_OUTPUT_CAP:].decode(errors="replace")
    _assert_competing_lease_is_blocked(lease_path)
    _cleanup_process_identities(owned_identities, timeout=5)
    _assert_lease_released(lease_path)
    return (
        stdout[-_OUTPUT_CAP:],
        stderr[-_OUTPUT_CAP:],
        observed_identity,
        time.monotonic() - started_at,
    )


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


def _rollouts_from_root(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("rollout-*")
        if path.is_file() and path.suffix in {".jsonl", ".zst"}
    )


def _rollouts(home: Path) -> list[Path]:
    return _rollouts_from_root(home / "sessions")


def _rollout_records(path: Path) -> list[dict[str, object]]:
    if path.suffix == ".zst":
        data = zstandard.ZstdDecompressor().decompress(path.read_bytes())
    else:
        data = path.read_bytes()
    lines = data.splitlines()
    assert lines
    records = [json.loads(line) for line in lines]
    assert all(isinstance(record, dict) for record in records)
    return records


def _recorded_thread_ids(records: list[dict[str, object]]) -> set[str]:
    result: set[str] = set()
    for record in records:
        if record.get("type") == "thread.started" and isinstance(record.get("thread_id"), str):
            result.add(str(record["thread_id"]))
        payload = record.get("payload")
        if (
            record.get("type") == "session_meta"
            and isinstance(payload, dict)
            and isinstance(payload.get("id"), str)
        ):
            result.add(str(payload["id"]))
    return result


def _write_history_profile(
    root: Path,
    *,
    project: Path,
    file_count: int,
    payload_bytes: int,
) -> dict[str, int]:
    timestamp = "2026-07-24T00:00:00.000Z"
    for index in range(file_count):
        thread_id = f"profile-thread-{index:04d}"
        records = [
            {
                "timestamp": timestamp,
                "type": "session_meta",
                "payload": {
                    "id": thread_id,
                    "timestamp": timestamp,
                    "cwd": str(project),
                    "originator": "codex_cli_rs",
                    "cli_version": "0.147.0",
                    "source": "cli",
                },
            },
            {
                "timestamp": timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "x" * payload_bytes}],
                },
            },
        ]
        path = root / "2026" / "07" / "24" / f"rollout-profile-{index:04d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"".join(
                json.dumps(record, separators=(",", ":")).encode() + b"\n" for record in records
            )
        )
    files = _rollouts_from_root(root)
    return {
        "file_count": len(files),
        "allocated_bytes": sum(path.stat().st_blocks * 512 for path in files),
    }


def _stage_history_profile(source_root: Path, generated_home: Path) -> list[Path]:
    staged: list[Path] = []
    for source in _rollouts_from_root(source_root):
        destination = generated_home / "sessions" / source.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, destination)
        staged.append(destination)
    return staged


def test_installed_codex_preserves_staged_rollout_inode_and_inherited_lease(
    tmp_path: Path,
) -> None:
    binary = _installed_supported_codex()
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ("git", "init", "--quiet"),
        cwd=project,
        check=True,
        capture_output=True,
    )
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
    fresh_stdout, fresh_stderr, fresh_live_identity, fresh_duration = _run_with_inherited_lease(
        fresh,
        project=project,
        lease_path=tmp_path / "fresh.lease",
        rollout_root=fresh_home / "sessions",
    )
    thread_id = _thread_id(fresh_stdout)
    fresh_rollouts = _rollouts(fresh_home)
    assert len(fresh_rollouts) == 1
    fresh_records = _rollout_records(fresh_rollouts[0])
    assert _recorded_thread_ids(fresh_records) == {thread_id}
    if fresh_rollouts[0].suffix == ".jsonl":
        assert (
            fresh_rollouts[0].stat().st_dev,
            fresh_rollouts[0].stat().st_ino,
        ) == fresh_live_identity

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
    resume_stdout, resume_stderr, resume_live_identity, resume_duration = (
        _run_with_inherited_lease(
            resume,
            project=project,
            lease_path=tmp_path / "resume.lease",
            rollout_root=resume_home / "sessions",
        )
    )
    assert _thread_id(resume_stdout) == thread_id
    final_rollouts = _rollouts(resume_home)
    assert len(final_rollouts) == 1
    final = final_rollouts[0]
    final_records = _rollout_records(final)
    assert _recorded_thread_ids(final_records) == {thread_id}
    assert final_records[: len(fresh_records)] == fresh_records
    assert resume_live_identity == staged_identity
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
                "fresh_duration_seconds": fresh_duration,
                "resume_duration_seconds": resume_duration,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (diagnostics / "fresh.stdout").write_bytes(fresh_stdout)
    (diagnostics / "fresh.stderr").write_bytes(fresh_stderr)
    (diagnostics / "resume.stdout").write_bytes(resume_stdout)
    (diagnostics / "resume.stderr").write_bytes(resume_stderr)


@pytest.mark.timeout(600)
def test_installed_codex_startup_profile_matrix_is_bounded_and_retained(
    tmp_path: Path,
) -> None:
    binary = _installed_supported_codex()
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ("git", "init", "--quiet"),
        cwd=project,
        check=True,
        capture_output=True,
    )
    diagnostics = project / ".autoskillit" / "temp" / uuid.uuid4().hex[:16]
    diagnostics.mkdir(parents=True)
    (tmp_path / "leases").mkdir()
    profile_defs = {
        "small": (2, 64),
        "many_file": (96, 64),
        "large_byte": (4, 256 * 1024),
    }
    profile_metadata = {
        name: _write_history_profile(
            tmp_path / "history" / name,
            project=project,
            file_count=file_count,
            payload_bytes=payload_bytes,
        )
        for name, (file_count, payload_bytes) in profile_defs.items()
    }
    backend = CodexBackend()
    sequence = 0

    def measure(profile_name: str, *, retain: bool) -> dict[str, object]:
        nonlocal sequence
        sequence += 1
        generated_home = tmp_path / "homes" / f"{sequence:02d}-{profile_name}"
        generated_home.parent.mkdir(exist_ok=True)
        _prepare_home(generated_home)
        staged_rollouts = _stage_history_profile(
            tmp_path / "history" / profile_name,
            generated_home,
        )
        assert len(staged_rollouts) == profile_metadata[profile_name]["file_count"]
        assert _rollouts(generated_home) == staged_rollouts
        spec = backend.build_headless_cmd(
            "Respond with exactly: autoskillit startup profile canary"
        )
        spec = replace(
            spec,
            cmd=(binary, *spec.cmd[1:]),
            env={**spec.env, "CODEX_HOME": str(generated_home)},
            cwd=str(project),
        )
        stdout, stderr, _, duration = _run_with_inherited_lease(
            spec,
            project=project,
            lease_path=tmp_path / "leases" / f"{sequence:02d}-{profile_name}.lease",
            rollout_root=generated_home / "sessions",
        )
        assert duration <= 17.0
        assert len(_rollouts(generated_home)) == len(staged_rollouts) + 1
        sample = {
            "sequence": sequence,
            "profile": profile_name,
            "duration_seconds": duration,
            **profile_metadata[profile_name],
        }
        if retain:
            (diagnostics / f"sample-{sequence:02d}.stdout").write_bytes(stdout[-_OUTPUT_CAP:])
            (diagnostics / f"sample-{sequence:02d}.stderr").write_bytes(stderr[-_OUTPUT_CAP:])
        return sample

    for profile_name in profile_defs:
        measure(profile_name, retain=False)

    retained: list[dict[str, object]] = []
    randomized_order: list[str] = []
    generator = random.Random(0xA5705)
    for _ in range(3):
        round_order = list(profile_defs)
        generator.shuffle(round_order)
        randomized_order.extend(round_order)
        retained.extend(measure(profile_name, retain=True) for profile_name in round_order)

    summaries: dict[str, dict[str, float | bool]] = {}
    for profile_name in profile_defs:
        durations = [
            float(sample["duration_seconds"])
            for sample in retained
            if sample["profile"] == profile_name
        ]
        median = statistics.median(durations)
        mad = statistics.median(abs(duration - median) for duration in durations)
        mean = statistics.fmean(durations)
        coefficient_of_variation = statistics.pstdev(durations) / mean if mean else 0.0
        summaries[profile_name] = {
            "median_seconds": median,
            "mad_seconds": mad,
            "coefficient_of_variation": coefficient_of_variation,
            "unstable": coefficient_of_variation > 0.25,
        }

    (diagnostics / "samples.json").write_text(
        json.dumps(retained, sort_keys=True),
        encoding="utf-8",
    )
    (diagnostics / "summary.json").write_text(
        json.dumps(
            {
                "codex_version": _SUPPORTED_VERSION,
                "profiles": profile_metadata,
                "retained_order": randomized_order,
                "retained_samples_per_profile": 3,
                "output_cap_bytes": _OUTPUT_CAP,
                "summaries": summaries,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
