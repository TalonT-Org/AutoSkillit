"""Tests for line_driver integration in run_managed_async and its runner wrappers.

Uses a small scripted LineDriver test double (not CodexAppServerDriver — that
state machine's own protocol semantics are covered by
tests/execution/backends/test_codex_app_server_driver.py) to isolate the
runner/tee plumbing: piped spawn, byte-exact capture, failure signaling,
spawn/reap callback interplay, and cleanup ordering.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import anyio
import pytest

from autoskillit.core import LineDriverError

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class _EchoDriver:
    """Sends one request; finishes once a response with a matching id arrives."""

    def __init__(self, *, request_id: int = 1) -> None:
        self._request_id = request_id
        self.finished = False
        self.failure: str | None = None

    def initial_lines(self) -> tuple[str, ...]:
        return (json.dumps({"id": self._request_id, "method": "ping", "params": {}}),)

    def on_line(self, line: str) -> tuple[str, ...]:
        obj = json.loads(line)
        if obj.get("id") == self._request_id and "result" in obj:
            self.finished = True
        return ()


class _RefusingDriver:
    """Fails the instant any line arrives — simulates an unsupported request."""

    def __init__(self) -> None:
        self.finished = False
        self.failure: str | None = None

    def initial_lines(self) -> tuple[str, ...]:
        return (json.dumps({"id": 1, "method": "probe", "params": {}}),)

    def on_line(self, line: str) -> tuple[str, ...]:
        del line
        self.failure = "server refused the catalog"
        return ()


# A well-behaved child: reads exactly one stdin line, echoes an ack, exits.
_ECHO_CHILD_SCRIPT = (
    "import sys, json\n"
    "line = sys.stdin.readline()\n"
    "obj = json.loads(line)\n"
    "sys.stdout.write(json.dumps({'id': obj['id'], 'result': {}}) + chr(10))\n"
    "sys.stdout.flush()\n"
)

# A child that responds once with an error, then deliberately keeps stdout
# open well past any reasonable test timeout — proving failure detection
# does not wait for EOF.
_KEEP_ALIVE_CHILD_SCRIPT = (
    "import sys, json, time\n"
    "line = sys.stdin.readline()\n"
    "obj = json.loads(line)\n"
    "sys.stdout.write(json.dumps({'id': obj['id'], 'error': "
    "{'code': -32601, 'message': 'nope'}}) + chr(10))\n"
    "sys.stdout.flush()\n"
    "time.sleep(30)\n"
)

# A child that exits immediately without ever reading or writing anything.
_IMMEDIATE_EXIT_CHILD_SCRIPT = "pass\n"

# A child that reads stdin but never responds (stdout stays silent) until
# killed — used to exercise the overall wall-clock timeout path.
_SILENT_CHILD_SCRIPT = "import sys, time\nsys.stdin.readline()\ntime.sleep(30)\n"


class TestValidationBeforeSpawn:
    async def test_pty_mode_with_line_driver_raises_before_spawning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.execution import process as process_module

        def _fail_if_called(*_args: object, **_kwargs: object) -> None:
            pytest.fail("spawn_owned_process must not be called when validation rejects the call")

        monkeypatch.setattr(process_module, "spawn_owned_process", _fail_if_called)

        with pytest.raises(ValueError, match="mutually exclusive"):
            await process_module.run_managed_async(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                pty_mode=True,
                line_driver=_EchoDriver(),
            )

    async def test_input_data_with_line_driver_raises_before_spawning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.execution import process as process_module

        def _fail_if_called(*_args: object, **_kwargs: object) -> None:
            pytest.fail("spawn_owned_process must not be called when validation rejects the call")

        monkeypatch.setattr(process_module, "spawn_owned_process", _fail_if_called)

        with pytest.raises(ValueError, match="mutually exclusive"):
            await process_module.run_managed_async(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                input_data="hello",
                line_driver=_EchoDriver(),
            )


class TestNormalCompletion:
    async def test_echo_child_returns_normal_result_with_captured_response(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.execution.process import run_managed_async

        result = await run_managed_async(
            [sys.executable, "-c", _ECHO_CHILD_SCRIPT],
            cwd=tmp_path,
            timeout=5.0,
            line_driver=_EchoDriver(),
        )
        assert result.returncode == 0
        obj = json.loads(result.stdout.strip())
        assert obj == {"id": 1, "result": {}}


class TestDriverFailure:
    async def test_failure_kills_promptly_and_captures_tail_output(self, tmp_path: Path) -> None:
        from autoskillit.execution.process import run_managed_async

        capture_dir = tmp_path / "capture"
        start = anyio.current_time()
        with pytest.raises(LineDriverError, match="server refused"):
            await run_managed_async(
                [sys.executable, "-c", _KEEP_ALIVE_CHILD_SCRIPT],
                cwd=tmp_path,
                timeout=30.0,
                line_driver=_RefusingDriver(),
                capture_dir=capture_dir,
            )
        elapsed = anyio.current_time() - start
        # The stub child sleeps for 30s after refusing; a prompt kill must
        # finish in a small fraction of that, proving EOF was never awaited.
        assert elapsed < 15.0

        captured = list(capture_dir.glob("proc_stdout_*"))
        assert len(captured) == 1
        text = captured[0].read_text()
        assert '"error"' in text
        assert "nope" in text


class TestChildExitsMidHandshake:
    async def test_child_exit_before_response_ends_tee_without_hanging(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.execution.process import run_managed_async

        with pytest.raises(LineDriverError, match="closed stdout"):
            await run_managed_async(
                [sys.executable, "-c", _IMMEDIATE_EXIT_CHILD_SCRIPT],
                cwd=tmp_path,
                timeout=5.0,
                line_driver=_EchoDriver(),
            )


class TestTimeoutWhileTeeBlocked:
    async def test_overall_timeout_kills_and_returns_promptly(self, tmp_path: Path) -> None:
        from autoskillit.execution.process import run_managed_async

        start = anyio.current_time()
        with pytest.raises(LineDriverError):
            await run_managed_async(
                [sys.executable, "-c", _SILENT_CHILD_SCRIPT],
                cwd=tmp_path,
                timeout=0.5,
                line_driver=_EchoDriver(),
            )
        elapsed = anyio.current_time() - start
        assert elapsed < 15.0


class TestSpawnReapCallbackMatrix:
    async def test_normal_finish_reports_spawn_and_reap(self, tmp_path: Path) -> None:
        from autoskillit.execution.process import run_managed_async

        events: list[tuple[str, int, int]] = []
        await run_managed_async(
            [sys.executable, "-c", _ECHO_CHILD_SCRIPT],
            cwd=tmp_path,
            timeout=5.0,
            line_driver=_EchoDriver(),
            on_process_spawned=lambda pid, pgid: events.append(("spawned", pid, pgid)),
            on_process_reaped=lambda pid, pgid: events.append(("reaped", pid, pgid)),
        )
        assert [kind for kind, _, _ in events] == ["spawned", "reaped"]

    async def test_driver_failure_still_reports_spawn_and_reap(self, tmp_path: Path) -> None:
        from autoskillit.execution.process import run_managed_async

        events: list[tuple[str, int, int]] = []
        with pytest.raises(LineDriverError):
            await run_managed_async(
                [sys.executable, "-c", _KEEP_ALIVE_CHILD_SCRIPT],
                cwd=tmp_path,
                timeout=30.0,
                line_driver=_RefusingDriver(),
                on_process_spawned=lambda pid, pgid: events.append(("spawned", pid, pgid)),
                on_process_reaped=lambda pid, pgid: events.append(("reaped", pid, pgid)),
            )
        assert [kind for kind, _, _ in events] == ["spawned", "reaped"]

    async def test_cancellation_settles_and_reports_reap_once(self, tmp_path: Path) -> None:
        from autoskillit.execution.process import run_managed_async

        spawned = anyio.Event()
        reaped: list[tuple[int, int]] = []

        async def run_until_cancelled() -> None:
            await run_managed_async(
                [sys.executable, "-c", _SILENT_CHILD_SCRIPT],
                cwd=tmp_path,
                timeout=30.0,
                line_driver=_EchoDriver(),
                on_process_spawned=lambda _pid, _pgid: spawned.set(),
                on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
            )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_until_cancelled)
            await spawned.wait()
            task_group.cancel_scope.cancel()

        assert len(reaped) == 1
        assert reaped[0][0] == reaped[0][1]

    async def test_incomplete_cleanup_omits_reap_callback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import dataclasses

        from autoskillit.execution import process as process_module

        original_execute = process_module.execute_termination_action

        async def incomplete_execute(*args: object, **kwargs: object):
            kill_reason, returncode, cleanup = await original_execute(*args, **kwargs)
            return (
                kill_reason,
                returncode,
                dataclasses.replace(cleanup, observation_complete=False),
            )

        monkeypatch.setattr(process_module, "execute_termination_action", incomplete_execute)
        reaped: list[tuple[int, int]] = []

        result = await process_module.run_managed_async(
            [sys.executable, "-c", _ECHO_CHILD_SCRIPT],
            cwd=tmp_path,
            timeout=5.0,
            line_driver=_EchoDriver(),
            on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
        )

        assert result.cleanup_evidence is not None
        assert result.cleanup_evidence.complete is False
        assert reaped == []


class TestDefaultSubprocessRunnerForwarding:
    async def test_default_runner_forwards_line_driver(self, tmp_path: Path) -> None:
        from autoskillit.execution.process._lifecycle.runner import DefaultSubprocessRunner

        runner = DefaultSubprocessRunner()
        result = await runner(
            [sys.executable, "-c", _ECHO_CHILD_SCRIPT],
            cwd=tmp_path,
            timeout=5.0,
            line_driver=_EchoDriver(),
        )
        obj = json.loads(result.stdout.strip())
        assert obj == {"id": 1, "result": {}}


class TestRecordingSubprocessRunnerValidation:
    async def test_rejects_line_driver_with_pty_mode_before_branch_selection(
        self, tmp_path: Path
    ) -> None:
        """PTY+step_name would otherwise dispatch straight to the cassette
        recorder, bypassing the inner runner (and its own validation)
        entirely — the recording wrapper must reject this itself."""
        from unittest.mock import MagicMock

        from autoskillit.execution.recording import RecordingSubprocessRunner

        runner = RecordingSubprocessRunner(recorder=MagicMock())
        with pytest.raises(ValueError, match="mutually exclusive"):
            await runner(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                env={"SCENARIO_STEP_NAME": "some-step"},
                pty_mode=True,
                line_driver=_EchoDriver(),
            )

    async def test_rejects_line_driver_with_input_data(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from autoskillit.execution.recording import RecordingSubprocessRunner

        runner = RecordingSubprocessRunner(recorder=MagicMock())
        with pytest.raises(ValueError, match="mutually exclusive"):
            await runner(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                input_data="hi",
                line_driver=_EchoDriver(),
            )


class TestDriveProcessIoUnit:
    """Direct synchronous tests of the tee, isolated from anyio/subprocess."""

    def _run(
        self,
        *,
        chunks: list[bytes],
        driver: object,
    ) -> tuple[list[bytes], bytes, bool]:
        import threading

        from autoskillit.execution.process._process_io import drive_process_io

        class _ScriptedReader:
            def __init__(self, data: list[bytes]) -> None:
                self._data = list(data)

            def read(self, size: int) -> bytes:
                del size
                return self._data.pop(0) if self._data else b""

            # drive_process_io calls read1(), not read() — see the read1()
            # docstring note in _process_io.py. Each scripted chunk already
            # models one discrete "whatever's immediately available" read,
            # so read1() reuses the exact same one-chunk-per-call behavior.
            def read1(self, size: int) -> bytes:
                return self.read(size)

        class _CapturingWriter:
            def __init__(self) -> None:
                self.written: list[bytes] = []
                self.closed = False

            def write(self, data: bytes) -> int:
                self.written.append(data)
                return len(data)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

        stdout_pipe = _ScriptedReader(chunks)
        stdin_pipe = _CapturingWriter()
        capture_file = io.BytesIO()
        tee_done = threading.Event()
        failures: list[str] = []

        drive_process_io(
            stdout_pipe=stdout_pipe,  # type: ignore[arg-type]
            stdin_pipe=stdin_pipe,  # type: ignore[arg-type]
            capture_file=capture_file,  # type: ignore[arg-type]
            driver=driver,  # type: ignore[arg-type]
            on_failure=failures.append,
            tee_done=tee_done,
        )
        assert tee_done.is_set()
        return stdin_pipe.written, capture_file.getvalue(), stdin_pipe.closed

    def test_initial_lines_written_with_exactly_one_trailing_newline(self) -> None:
        driver = _EchoDriver()
        written, _capture, _closed = self._run(chunks=[b""], driver=driver)
        assert written == [json.dumps({"id": 1, "method": "ping", "params": {}}).encode() + b"\n"]

    def test_response_lines_written_are_byte_exact_with_one_newline_each(self) -> None:
        driver = _EchoDriver()
        response = json.dumps({"id": 1, "result": {}}).encode() + b"\n"
        written, _capture, closed = self._run(chunks=[response, b""], driver=driver)
        # first entry is the initial ping; the driver returns no lines on
        # a finishing response, so no further stdin writes occur.
        assert written[0].count(b"\n") == 1
        assert closed  # driver finished -> stdin closed

    def test_capture_is_byte_exact_including_non_utf8(self) -> None:
        driver = _EchoDriver()
        non_utf8_tail = b"\xff\xfe partial"
        chunks = [b'{"id": 1, "result": {}}\n' + non_utf8_tail, b""]
        _written, capture, _closed = self._run(chunks=chunks, driver=driver)
        assert capture == b"".join(chunks)

    def test_unterminated_trailing_fragment_preserved_without_synthetic_newline(self) -> None:
        driver = _EchoDriver()
        fragment = b'{"id": 1, "result"'  # never completed with a newline
        _written, capture, _closed = self._run(chunks=[fragment, b""], driver=driver)
        assert capture == fragment
        assert not capture.endswith(b"\n")

    def test_eof_before_completion_reports_failure_via_callback(self) -> None:
        import threading

        from autoskillit.execution.process._process_io import drive_process_io

        class _ScriptedReader:
            def __init__(self, data: list[bytes]) -> None:
                self._data = list(data)

            def read(self, size: int) -> bytes:
                del size
                return self._data.pop(0) if self._data else b""

            # See the sibling _ScriptedReader above: drive_process_io calls
            # read1(), and one scripted chunk per call already models that.
            def read1(self, size: int) -> bytes:
                return self.read(size)

        class _CapturingWriter:
            def write(self, data: bytes) -> int:
                return len(data)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        driver = _EchoDriver()
        failures: list[str] = []
        drive_process_io(
            stdout_pipe=_ScriptedReader([b""]),  # type: ignore[arg-type]
            stdin_pipe=_CapturingWriter(),  # type: ignore[arg-type]
            capture_file=io.BytesIO(),
            driver=driver,  # type: ignore[arg-type]
            on_failure=failures.append,
            tee_done=threading.Event(),
        )
        assert len(failures) == 1
        assert "closed stdout" in failures[0]

    def test_unsupported_request_response_written_before_failure_checked(self) -> None:
        """The error response line must be written even though the same
        on_line call also sets failure — order matters (D1: send the
        response before closing stdin)."""

        class _ImmediateRefusal:
            def __init__(self) -> None:
                self.finished = False
                self.failure: str | None = None

            def initial_lines(self) -> tuple[str, ...]:
                return ()

            def on_line(self, line: str) -> tuple[str, ...]:
                obj = json.loads(line)
                self.failure = f"unsupported {obj.get('method')}"
                return (json.dumps({"id": obj["id"], "error": {"code": -32601}}),)

        request = json.dumps({"id": "srv-1", "method": "some/unavailable"}).encode() + b"\n"
        written, _capture, closed = self._run(chunks=[request, b""], driver=_ImmediateRefusal())
        assert any(b'"error"' in line for line in written)
        assert closed
