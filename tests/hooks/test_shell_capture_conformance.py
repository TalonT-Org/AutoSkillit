"""Executable conformance matrix for the shell capture runner.

Every invariant names its baseline in ``tests/hooks/_shell_conformance_matrix.py``
and in ADR-0008 § Execution conformance matrix.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import autoskillit.hooks._capture_artifacts as capture_artifacts
import autoskillit.hooks.shell_capture_hook as shell_capture_hook
from autoskillit.core import (
    ManagedHeadlessSessionKind,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
from autoskillit.hooks._capture._types import HOT_PATH_LOCK_WAIT
from autoskillit.hooks._capture_artifacts import open_capture_lifecycle
from autoskillit.hooks._capture_contract import (
    CAPTURE_REQUEST_PROTOCOL_VERSION,
    CaptureFailureReason,
    CaptureFailureV3,
    CaptureLineageRef,
    CaptureRequest,
    CaptureV2Fields,
    encode_capture_request,
    parse_capture_failure_v3,
    parse_capture_v2,
)
from autoskillit.hooks._capture_lifecycle import CaptureState
from autoskillit.hooks.shell_capture_hook import _build_harness
from tests.conftest import production_interpreter_env
from tests.hooks._shell_conformance_matrix import (
    CONFORMANCE_CASES,
    ConformanceCaseDef,
    ConformanceDriver,
    ConformanceExpectation,
    ConformanceMode,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_INLINE_BYTES = 12_000
_CAPTURE_SUBDIR = ".autoskillit/temp/shell_capture"
_TIMEOUT = 30
_DIRECT_LAUNCH_ID = "1" * 32
_DIRECT_ATTEMPT_ID = "2" * 32
_HARNESS_FORBIDDEN_VERBS: frozenset[str] = frozenset(
    {
        "rm",
        "unlink",
        "shred",
        "truncate",
        "rmdir",
        "mv",  # moving the capture file would break concurrent reads
    }
)

_NESTED_WRAP_INNER = "echo hi"


def _make_project_dirs(tmp_path: Path) -> None:
    (tmp_path / _CAPTURE_SUBDIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "temp" / "investigate").mkdir(parents=True, exist_ok=True)
    (tmp_path / "x.jsonl").write_text('{"a":1}\n{"b":2}\n')


def _capture_dir(tmp_path: Path) -> Path:
    return tmp_path / _CAPTURE_SUBDIR


def _artifact_files(tmp_path: Path) -> list[Path]:
    return sorted(_capture_dir(tmp_path).glob("shell_*.log"))


def _wait_for_path(path: Path, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


def _parse_single_capture_v2(output: bytes) -> CaptureV2Fields:
    candidates = [
        line for line in output.splitlines() if line.startswith(b"[AutoSkillit shell capture v2:")
    ]
    assert len(candidates) == 1
    return parse_capture_v2(candidates[0])


def _parse_single_failure_v3(output: bytes) -> CaptureFailureV3:
    candidates = [
        line
        for line in output.splitlines()
        if line.startswith(b"[AutoSkillit shell capture failure v3:")
    ]
    assert len(candidates) == 1
    return parse_capture_failure_v3(candidates[0])


def _assert_published_capture_v2(project: Path, output: bytes, expected: bytes) -> None:
    parsed = _parse_single_capture_v2(output)
    assert parsed.reference_status == "published"
    assert parsed.reference is not None
    assert parsed.total_bytes == len(expected)
    assert parsed.sha256 == hashlib.sha256(expected).hexdigest()
    assert b"complete=true" not in output
    assert b".log" not in output
    chunks: list[bytes] = []
    with open_capture_lifecycle(
        str(project),
        create=False,
        lock_wait=HOT_PATH_LOCK_WAIT,
    ) as lifecycle:
        with lifecycle.open_verified_capture(parsed.reference) as reader:
            offset = 0
            while offset < parsed.total_bytes:
                chunk = reader.read(offset, min(64 * 1024, parsed.total_bytes - offset))
                assert chunk
                chunks.append(chunk)
                offset += len(chunk)
    assert b"".join(chunks) == expected


def _run_raw(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


def _run_raw_merged(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


def _run_wrapped(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    wrapped = _build_harness(command, str(tmp_path), uuid4().hex[:16])
    return subprocess.run(
        ["bash", "-c", wrapped],
        capture_output=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


def _assert_shell_status(
    raw: subprocess.CompletedProcess[bytes],
    runner: subprocess.CompletedProcess[bytes],
) -> None:
    expected = 128 + (-raw.returncode) if raw.returncode < 0 else raw.returncode
    assert runner.returncode == expected


def _assert_expectation(
    case: ConformanceCaseDef,
    mode: ConformanceMode,
    raw: subprocess.CompletedProcess[bytes],
    runner: subprocess.CompletedProcess[bytes],
    projects: dict[str, Path],
) -> None:
    expectation = case.expect[mode]
    runner_project = projects[mode.value]

    if expectation is ConformanceExpectation.SHELL_SIGNAL_STATUS:
        assert raw.returncode < 0
        expectation = ConformanceExpectation.RAW_OUTPUT_AND_STATUS

    if expectation is ConformanceExpectation.RAW_OUTPUT_AND_STATUS:
        _assert_shell_status(raw, runner)
        if mode is ConformanceMode.CAPTURE:
            expected = raw.stdout
            assert raw.stderr is None
            assert runner.stderr == b""
            artifacts = _artifact_files(runner_project)
            assert len(artifacts) == 1
            assert artifacts[0].read_bytes() == expected
            if len(expected) <= _INLINE_BYTES:
                assert runner.stdout == expected
            else:
                _assert_published_capture_v2(runner_project, runner.stdout, expected)
        else:
            assert runner.stdout == raw.stdout
            assert runner.stderr == raw.stderr
            assert not _artifact_files(runner_project)
        return

    if expectation is ConformanceExpectation.LATE_PIPE_BYTES_INCLUDED:
        assert mode is ConformanceMode.CAPTURE
        _assert_shell_status(raw, runner)
        assert b"late" in raw.stdout
        assert runner.stdout == raw.stdout
        assert runner.stderr == b""
        assert _artifact_files(runner_project)[0].read_bytes() == raw.stdout
        return

    if expectation is ConformanceExpectation.LATE_PIPE_BYTES_SETTLED:
        assert mode is ConformanceMode.DIRECT
        _assert_shell_status(raw, runner)
        early = raw.stdout.partition(b"late")[0]
        assert early and b"late" in raw.stdout
        assert early in runner.stdout
        assert b"late" not in runner.stdout
        assert runner.stderr == raw.stderr
        return

    if expectation is ConformanceExpectation.MARKER_SETTLED:
        _assert_shell_status(raw, runner)
        assert _wait_for_path(projects["raw"] / "marker", timeout=1.5)
        time.sleep(1.0)
        assert not (runner_project / "marker").exists()
        return

    if expectation is ConformanceExpectation.MARKER_WRITTEN:
        _assert_shell_status(raw, runner)
        assert _wait_for_path(projects["raw"] / "marker", timeout=1.5)
        assert _wait_for_path(runner_project / "marker", timeout=1.5)
        return

    raise AssertionError(f"unhandled conformance expectation: {expectation}")


def _write_detached_pipe_helper(tmp_path: Path) -> Path:
    helper = tmp_path / "detached_pipe_child.py"
    helper.write_text(
        """import os
import socket
import sys

mode, host, port = sys.argv[1:]
parent_read, parent_write = os.pipe()
ready_read, ready_write = os.pipe()
pid = os.fork()
if pid:
    os.close(parent_read)
    os.close(ready_write)
    if os.read(ready_read, 1) != b"R":
        os._exit(2)
    os.close(ready_read)
    os.write(1, b"early\\n")
    os._exit(0)

os.close(parent_write)
os.close(ready_read)
os.setsid()
os.write(ready_write, b"R")
os.close(ready_write)
while os.read(parent_read, 1):
    pass
os.close(parent_read)
if mode == "closed":
    os.close(1)
    os.close(2)
barrier = socket.create_connection((host, int(port)))
barrier.sendall(f"{os.getpid()}\\n".encode())
if barrier.recv(1) != b"R":
    os._exit(2)
if mode == "retained":
    os.write(1, b"late\\n")
barrier.close()
os._exit(0)
"""
    )
    return helper


def _accept_detached_child(server: socket.socket) -> tuple[socket.socket, int]:
    connection, _address = server.accept()
    connection.settimeout(_TIMEOUT)
    message = bytearray()
    while b"\n" not in message:
        chunk = connection.recv(64)
        if not chunk or len(message) + len(chunk) > 64:
            connection.close()
            raise AssertionError("detached child did not provide a bounded PID")
        message.extend(chunk)
    line, separator, remainder = bytes(message).partition(b"\n")
    if not separator or remainder or not line.isdigit():
        connection.close()
        raise AssertionError("detached child PID message is invalid")
    return connection, int(line)


def _release_detached_child(
    connection: socket.socket | None,
    child_pid: int | None,
) -> None:
    if connection is None:
        return
    released = False
    try:
        connection.sendall(b"R")
        while connection.recv(64):
            pass
        released = True
    except OSError:
        pass
    finally:
        connection.close()
    if not released and child_pid is not None:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _main_generated_wrapper(command: str, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    event = {"cwd": str(cwd), "tool_input": {"command": command}}
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    output = io.StringIO()
    with pytest.raises(SystemExit) as exit_info, redirect_stdout(output):
        shell_capture_hook.main()
    assert exit_info.value.code == 0
    payload = json.loads(output.getvalue())
    return payload["hookSpecificOutput"]["updatedInput"]["command"]


def _direct_lineage_reference(project: Path) -> CaptureLineageRef:
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = store.create(
        lineage_anchor=project,
        launch_id=_DIRECT_LAUNCH_ID,
        decision=resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT),
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    lineage = store.append_attempt(
        lineage_anchor=project,
        launch_id=lineage.launch_id,
        attempt_id=_DIRECT_ATTEMPT_ID,
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    return CaptureLineageRef(
        schema_version=lineage.reference.schema_version,
        launch_id=lineage.reference.launch_id,
        lineage_digest=lineage.reference.lineage_digest,
        lineage_anchor=lineage.reference.lineage_anchor,
        anchor_device=lineage.reference.anchor_device,
        anchor_inode=lineage.reference.anchor_inode,
    )


def _runner_argv(
    command: str,
    project: Path,
    *,
    mode: str,
) -> list[str]:
    lineage_ref = _direct_lineage_reference(project) if mode == "direct" else None
    request = CaptureRequest(
        protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
        action="run",
        mode=mode,
        attempt_id=_DIRECT_ATTEMPT_ID if lineage_ref is not None else None,
        lineage_ref=lineage_ref,
        cwd=str(project),
        capture_id=uuid4().hex[:16],
        command=command,
    )
    return [
        sys.executable,
        "-I",
        str(Path(capture_artifacts.__file__).resolve()),
        encode_capture_request(request),
    ]


def _run_runner(
    command: str,
    project: Path,
    *,
    mode: str,
    execution_dir: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if execution_dir is None:
        execution_dir = project
    else:
        assert not execution_dir.samefile(project)
    return subprocess.run(
        _runner_argv(command, project, mode=mode),
        env=production_interpreter_env(),
        capture_output=True,
        cwd=execution_dir,
        timeout=_TIMEOUT,
        check=False,
    )


@pytest.mark.parametrize(
    ("case", "mode"),
    [
        pytest.param(case, mode, id=f"{case.id}-{mode.value}")
        for case in CONFORMANCE_CASES
        if case.driver is ConformanceDriver.COMMAND
        for mode in ConformanceMode
    ],
)
def test_command_conformance(
    tmp_path: Path,
    case: ConformanceCaseDef,
    mode: ConformanceMode,
) -> None:
    if case.id == "rg_sort" and shutil.which("rg") is None:
        pytest.skip("rg not available")
    if case.id == "jq_keys" and shutil.which("jq") is None:
        pytest.skip("jq not available")

    projects = {name: tmp_path / name for name in ("raw", "capture", "direct")}
    for project in projects.values():
        _make_project_dirs(project)

    expectation = case.expect[mode]
    marker_case = expectation in {
        ConformanceExpectation.MARKER_SETTLED,
        ConformanceExpectation.MARKER_WRITTEN,
    }
    if mode is ConformanceMode.DIRECT or marker_case:
        raw = _run_raw(case.command, projects["raw"])
    else:
        raw = _run_raw_merged(case.command, projects["raw"])

    runner = _run_runner(case.command, projects[mode.value], mode=mode.value)
    if mode is ConformanceMode.CAPTURE:
        assert len(_artifact_files(projects[mode.value])) == 1
    else:
        assert not _artifact_files(projects[mode.value])
    _assert_expectation(case, mode, raw, runner, projects)


def test_nested_harness_runs_inner_command_once(tmp_path: Path) -> None:
    _make_project_dirs(tmp_path)
    command = _build_harness(_NESTED_WRAP_INNER, str(tmp_path), uuid4().hex[:16])
    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == 0
    assert wrapped.stdout.count(b"hi\n") == 1


def _proc_gone_or_zombie(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return True
    state_fields = stat[stat.rfind(")") + 1 :].split()
    return bool(state_fields) and state_fields[0] == "Z"


def test_proc_disappearance_during_stat_read_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(_path: Path) -> str:
        raise ProcessLookupError("process exited during procfs read")

    monkeypatch.setattr(Path, "read_text", vanished)
    assert _proc_gone_or_zombie(123)


def _wait_for_proc_gone_or_zombie(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _proc_gone_or_zombie(pid):
            return True
        time.sleep(0.02)
    return _proc_gone_or_zombie(pid)


@pytest.mark.parametrize(
    ("case", "mode"),
    [
        pytest.param(case, mode, id=f"{case.id}-{mode.value}")
        for case in CONFORMANCE_CASES
        if case.driver in {ConformanceDriver.HOST_SIGKILL, ConformanceDriver.HOST_SIGTERM}
        for mode in ConformanceMode
    ],
)
def test_host_lifetime_conformance(
    tmp_path: Path,
    case: ConformanceCaseDef,
    mode: ConformanceMode,
) -> None:
    if not Path("/proc/self/stat").is_file():
        pytest.skip("host-lifetime oracle requires /proc")

    project = tmp_path / mode.value
    _make_project_dirs(project)
    if mode is ConformanceMode.CAPTURE:
        argv = ["bash", "-c", _build_harness(case.command, str(project), uuid4().hex[:16])]
        environment = None
    else:
        argv = _runner_argv(case.command, project, mode=mode.value)
        environment = production_interpreter_env()

    host = subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project,
        env=environment,
    )
    leader_pid: int | None = None
    try:
        leader_file = project / "leader.pid"
        assert _wait_for_path(leader_file, timeout=5.0)
        leader_pid = int(leader_file.read_text().strip())
        host_signal = (
            signal.SIGKILL if case.driver is ConformanceDriver.HOST_SIGKILL else signal.SIGTERM
        )
        os.killpg(host.pid, host_signal)
        try:
            host.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(host.pid, signal.SIGKILL)
            host.communicate(timeout=5.0)

        assert _wait_for_proc_gone_or_zombie(leader_pid, timeout=2.0)
        time.sleep(1.2)
        assert not (project / "marker").exists()
    finally:
        if host.poll() is None:
            try:
                os.killpg(host.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            host.communicate(timeout=5.0)
        if leader_pid is not None:
            try:
                os.killpg(leader_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize(
    (
        "requested_mode",
        "project_policy_disabled",
        "execution_policy_disabled",
        "expect_capture",
    ),
    [
        ("capture", False, True, True),
        ("direct", False, True, False),
        ("capture", True, False, False),
    ],
    ids=("capture", "lineage-direct", "project-policy-disabled"),
)
def test_runner_keeps_project_and_execution_authorities_distinct(
    tmp_path: Path,
    requested_mode: str,
    project_policy_disabled: bool,
    execution_policy_disabled: bool,
    expect_capture: bool,
) -> None:
    project = tmp_path / "project"
    execution_dir = tmp_path / "execution"
    project.mkdir()
    execution_dir.mkdir()
    for authority, disabled in (
        (project, project_policy_disabled),
        (execution_dir, execution_policy_disabled),
    ):
        config = authority / ".autoskillit" / "temp" / ".hook_config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"output_budget_policy": {"disabled": disabled}}))

    completed = _run_runner(
        "printf 'pwd=%s\\n' \"$PWD\"; printf ran > execution-sentinel",
        project,
        mode=requested_mode,
        execution_dir=execution_dir,
    )

    assert completed.returncode == 0
    assert f"pwd={execution_dir.resolve()}\n".encode() in completed.stdout
    assert (execution_dir / "execution-sentinel").read_text() == "ran"
    assert not (project / "execution-sentinel").exists()
    assert not _artifact_files(execution_dir)

    project_artifacts = _artifact_files(project)
    assert bool(project_artifacts) is expect_capture
    if expect_capture:
        assert len(project_artifacts) == 1
        capture_id = project_artifacts[0].stem.removeprefix("shell_")
        with open_capture_lifecycle(
            str(project),
            create=False,
            lock_wait=HOT_PATH_LOCK_WAIT,
        ) as lifecycle:
            record = lifecycle.get_record(capture_id)
        assert record is not None
        assert record.state is CaptureState.FINALIZED
        assert (_capture_dir(project) / ".capture-lifecycle.ledger").is_file()
        assert not _capture_dir(execution_dir).exists()
    else:
        assert not _capture_dir(project).exists()

    if requested_mode == "direct":
        store = DefaultManagedHeadlessSessionLineageStore()
        lineage = store.load(lineage_anchor=project, launch_id=_DIRECT_LAUNCH_ID)
        collected = store.collect_runner_observations(lineage.reference)
        assert len(collected.observations) == 1
        observation = collected.observations[0]
        assert observation.effective_mode is NativeShellCaptureMode.DIRECT
        assert observation.reason.value == "launch_authorized_direct"
        assert not observation.project_policy_disabled


def test_retained_pipe_waits_for_actual_eof_and_includes_late_bytes(
    tmp_path: Path,
) -> None:
    _make_project_dirs(tmp_path)
    helper = _write_detached_pipe_helper(tmp_path)
    capture_id = "0123456789abcdef"
    connection: socket.socket | None = None
    child_pid: int | None = None
    process: subprocess.Popen[bytes] | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.settimeout(_TIMEOUT)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        command = "exec " + shlex.join(
            [sys.executable, str(helper), "retained", "127.0.0.1", str(port)]
        )
        wrapped = _build_harness(command, str(tmp_path), capture_id)
        try:
            process = subprocess.Popen(
                ["bash", "-c", wrapped],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            connection, child_pid = _accept_detached_child(server)

            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
            with open_capture_lifecycle(
                str(tmp_path),
                create=False,
                lock_wait=HOT_PATH_LOCK_WAIT,
            ) as lifecycle:
                pending = lifecycle.get_record(capture_id)
            assert pending is not None
            assert pending.state is CaptureState.PUBLISHED_WRITING
            assert pending.manifest is None

            _release_detached_child(connection, child_pid)
            connection = None
            child_pid = None
            stdout, stderr = process.communicate(timeout=_TIMEOUT)

            expected = b"early\nlate\n"
            assert process.returncode == 0
            assert stdout == expected
            assert stderr == b""
            artifacts = _artifact_files(tmp_path)
            assert len(artifacts) == 1
            assert artifacts[0].read_bytes() == expected
            with open_capture_lifecycle(
                str(tmp_path),
                create=False,
                lock_wait=HOT_PATH_LOCK_WAIT,
            ) as lifecycle:
                finalized = lifecycle.get_record(capture_id)
            assert finalized is not None and finalized.manifest is not None
            assert finalized.state is CaptureState.FINALIZED
            assert finalized.manifest.total_bytes == len(expected)
            assert finalized.manifest.sha256 == hashlib.sha256(expected).hexdigest()
            assert finalized.manifest.inline_length == len(expected)
            assert finalized.manifest.head_length == len(expected)
            assert finalized.manifest.tail_length == len(expected)
        finally:
            _release_detached_child(connection, child_pid)
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=_TIMEOUT)


def test_detached_child_with_closed_pipe_does_not_delay_finalization(
    tmp_path: Path,
) -> None:
    _make_project_dirs(tmp_path)
    helper = _write_detached_pipe_helper(tmp_path)
    capture_id = "fedcba9876543210"
    connection: socket.socket | None = None
    child_pid: int | None = None
    process: subprocess.Popen[bytes] | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.settimeout(_TIMEOUT)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        command = "exec " + shlex.join(
            [sys.executable, str(helper), "closed", "127.0.0.1", str(port)]
        )
        wrapped = _build_harness(command, str(tmp_path), capture_id)
        try:
            process = subprocess.Popen(
                ["bash", "-c", wrapped],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            connection, child_pid = _accept_detached_child(server)
            stdout, stderr = process.communicate(timeout=_TIMEOUT)

            assert process.returncode == 0
            assert stdout == b"early\n"
            assert stderr == b""
            with open_capture_lifecycle(
                str(tmp_path),
                create=False,
                lock_wait=HOT_PATH_LOCK_WAIT,
            ) as lifecycle:
                finalized = lifecycle.get_record(capture_id)
            assert finalized is not None and finalized.manifest is not None
            assert finalized.state is CaptureState.FINALIZED
            assert finalized.manifest.total_bytes == len(stdout)
            assert finalized.manifest.sha256 == hashlib.sha256(stdout).hexdigest()
        finally:
            _release_detached_child(connection, child_pid)
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=_TIMEOUT)


def test_interleaved_stdout_stderr_ordering(tmp_path: Path) -> None:
    """Verify harness preserves interleaved stdout+stderr ordering via 2>&1."""
    command = "echo out1; echo err1 >&2; echo out2; echo err2 >&2; echo out3"
    _make_project_dirs(tmp_path)

    raw_merged = _run_raw_merged(command, tmp_path)
    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == raw_merged.returncode == 0

    artifacts = _artifact_files(tmp_path)
    if artifacts:
        actual = artifacts[0].read_bytes()
    else:
        actual = wrapped.stdout

    assert actual == raw_merged.stdout, (
        f"Interleaved ordering mismatch.\n  raw_merged={raw_merged.stdout!r}\n  actual={actual!r}"
    )


def test_capture_dir_uncreatable_fail_stops(tmp_path: Path) -> None:
    blocking_dir = tmp_path / ".autoskillit" / "temp"
    blocking_dir.mkdir(parents=True)
    (blocking_dir / "shell_capture").write_text("not a directory")

    command = "echo should_not_run"
    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == 1
    combined_bytes = wrapped.stdout + wrapped.stderr
    combined = combined_bytes.decode(errors="replace")
    assert '"status":"capture_failed"' in combined
    assert (
        _parse_single_failure_v3(combined_bytes).reason
        is CaptureFailureReason.FILESYSTEM_AUTHORITY
    )
    assert "should_not_run" not in combined


@pytest.mark.parametrize("component", [".autoskillit", "temp", "shell_capture"])
def test_main_generated_wrapper_rejects_symlinked_capture_components(
    component: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    secret = external / "secret"
    secret.write_text("must-not-be-read")

    parent = project
    for name in (".autoskillit", "temp", "shell_capture"):
        candidate = parent / name
        if name == component:
            candidate.symlink_to(external, target_is_directory=True)
            break
        candidate.mkdir()
        parent = candidate
    if component == "temp":
        (external / ".hook_config.json").write_text(
            json.dumps({"output_budget_policy": {"disabled": True}})
        )

    wrapper = _main_generated_wrapper(
        "printf ran > command_ran",
        project,
        monkeypatch,
    )
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 1
    combined = (completed.stdout + completed.stderr).encode()
    assert '"status":"capture_failed"' in completed.stdout + completed.stderr
    assert _parse_single_failure_v3(combined).reason is CaptureFailureReason.FILESYSTEM_AUTHORITY
    assert not (project / "command_ran").exists()
    assert not list(external.glob("shell_*.log"))
    assert secret.read_text() == "must-not-be-read"
    assert "must-not-be-read" not in completed.stdout + completed.stderr


def test_main_generated_wrapper_accepts_symlinked_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)

    wrapper = _main_generated_wrapper("printf anchored", supplied_cwd, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=supplied_cwd,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    assert completed.stdout == b"anchored"
    assert len(_artifact_files(project)) == 1


@pytest.mark.parametrize("collision", ["symlink", "hardlink", "regular"])
def test_main_generated_wrapper_rejects_final_artifact_collisions(
    collision: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    capture_id = "a1b2c3d4e5f60718"
    monkeypatch.setattr(
        shell_capture_hook,
        "uuid4",
        lambda: SimpleNamespace(hex=capture_id + "0" * 16),
    )
    artifact = _capture_dir(project) / f"shell_{capture_id}.log"
    external = tmp_path / "external-secret"
    external.write_bytes(b"must-survive")
    if collision == "symlink":
        artifact.symlink_to(external)
    elif collision == "hardlink":
        try:
            os.link(external, artifact)
        except OSError:
            pytest.skip("hardlinks unavailable")
    else:
        artifact.write_bytes(b"existing")

    wrapper = _main_generated_wrapper(
        "printf ran > command_ran",
        project,
        monkeypatch,
    )
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 1
    combined = (completed.stdout + completed.stderr).decode()
    assert '"status":"capture_failed"' in combined
    assert "must-survive" not in combined
    assert not (project / "command_ran").exists()
    assert external.read_bytes() == b"must-survive"
    if collision == "regular":
        assert artifact.read_bytes() == b"existing"


def test_capture_directory_replacement_uses_open_fds_and_hides_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    config = project / ".autoskillit" / "temp" / ".hook_config.json"
    config.write_text(json.dumps({"output_budget_policy": {"shell_max_inline_bytes": 8}}))
    external = tmp_path / "replacement-target"
    external.mkdir()
    command = (
        "mv .autoskillit/temp/shell_capture "
        ".autoskillit/temp/shell_capture-original; "
        f"ln -s {shlex.quote(str(external))} .autoskillit/temp/shell_capture; "
        "printf 0123456789abcdef"
    )

    wrapper = _main_generated_wrapper(command, project, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    expected = b"0123456789abcdef"
    parsed = _parse_single_capture_v2(completed.stdout)
    assert parsed.reference_status == "unavailable"
    assert parsed.reference is None
    assert parsed.unavailable_reason == "PUBLICATION_BINDING_UNAVAILABLE"
    assert parsed.total_bytes == len(expected)
    assert parsed.sha256 == hashlib.sha256(expected).hexdigest()
    assert b"complete=true" not in completed.stdout
    assert completed.stdout.startswith(expected[:5])
    assert completed.stdout.endswith(expected[-3:])
    displaced = project / ".autoskillit" / "temp" / "shell_capture-original"
    artifacts = sorted(displaced.glob("shell_*.log"))
    assert len(artifacts) == 1
    assert artifacts[0].read_bytes() == expected
    assert not list(external.iterdir())


def test_capture_artifact_replacement_uses_open_fd_and_hides_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    config = project / ".autoskillit" / "temp" / ".hook_config.json"
    config.write_text(json.dumps({"output_budget_policy": {"shell_max_inline_bytes": 8}}))
    capture_id = "1029384756abcdef"
    monkeypatch.setattr(
        shell_capture_hook,
        "uuid4",
        lambda: SimpleNamespace(hex=capture_id + "0" * 16),
    )
    capture_dir = _capture_dir(project)
    artifact = capture_dir / f"shell_{capture_id}.log"
    displaced = capture_dir / "opened-artifact.log"
    external = tmp_path / "external-target"
    external.write_bytes(b"must-survive")
    command = (
        f"mv {shlex.quote(str(artifact))} {shlex.quote(str(displaced))}; "
        f"ln -s {shlex.quote(str(external))} {shlex.quote(str(artifact))}; "
        "printf fedcba9876543210"
    )

    wrapper = _main_generated_wrapper(command, project, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    expected = b"fedcba9876543210"
    parsed = _parse_single_capture_v2(completed.stdout)
    assert parsed.reference_status == "unavailable"
    assert parsed.reference is None
    assert parsed.unavailable_reason == "PUBLICATION_BINDING_UNAVAILABLE"
    assert parsed.total_bytes == len(expected)
    assert parsed.sha256 == hashlib.sha256(expected).hexdigest()
    assert completed.stdout.startswith(expected[:5])
    assert completed.stdout.endswith(expected[-3:])
    assert displaced.read_bytes() == expected
    assert external.read_bytes() == b"must-survive"
    assert artifact.is_symlink()


@pytest.mark.parametrize(
    "cmd",
    ["echo hello", "ls -la", "cat /dev/null", "python3 -c 'print(1)'", ""],
)
def test_harness_contains_no_destructive_verbs(cmd: str) -> None:
    """Arch guard: hook-generated shell must not contain destructive verbs.

    Codex's exec-policy engine evaluates the full rewritten command, including
    hook-injected scaffolding. Destructive verbs (rm, unlink, etc.) are forbidden
    by Codex's built-in policy. This test ensures the harness never introduces them.
    """
    harness = _build_harness(cmd, "/tmp/test", uuid4().hex[:16])
    for raw_line in harness.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        first = (
            line.split(";", 1)[0].split("&&", 1)[0].split("||", 1)[0].strip().split(maxsplit=1)[0]
        )
        first = first.removeprefix("{").removeprefix("(")
        first = first.strip("\"'")
        assert first not in _HARNESS_FORBIDDEN_VERBS, (
            f"forbidden verb {first!r} found in harness line: {raw_line!r}\n"
            f"Generated from command: {cmd!r}"
        )
