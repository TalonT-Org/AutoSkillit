"""LineDriverSession: the full line_driver launch lifecycle, out of run_managed_async.

Kept in ``_lifecycle/`` rather than added to the already-11-file
``execution/process/`` root — placement, not a raised ceiling, is the
answer to a full package budget (the same call #4930 already established
for that package; see ``tests/arch/test_subpackage_isolation_file_counts.py``).
One object now owns every line_driver concern — spawn stdio selection, tee
task participation, termination-decision input, and settle/raise — so
``run_managed_async`` only ever calls a handful of methods on it.
"""

from __future__ import annotations

import functools
import subprocess
import threading
from typing import IO, Any

import anyio
import anyio.abc

from autoskillit.core import LineDriver, LineDriverError
from autoskillit.execution.process._process_io import drive_process_io


class LineDriverSession:
    """Owns one launch's line_driver lifecycle end to end.

    Every method is a safe no-op when *line_driver* is ``None`` (the
    overwhelming majority of launches), so callers never need their own
    ``if use_line_driver`` guard beyond the one at construction time.
    """

    def __init__(
        self, line_driver: LineDriver | None, *, pty_mode: bool, input_data: str | None
    ) -> None:
        if line_driver is not None and (pty_mode or input_data is not None):
            raise ValueError("line_driver is mutually exclusive with pty_mode and input_data")
        self.enabled = line_driver is not None
        self._line_driver = line_driver
        self._stdout_pipe: IO[bytes] | None = None
        self._stdin_pipe: IO[bytes] | None = None
        self.done = threading.Event()
        self.failure_ref: list[str | None] = [None]

    def spawn_stdout_kwarg(self, stdout_file: IO[bytes]) -> IO[Any] | int:
        return subprocess.PIPE if self.enabled else stdout_file

    def spawn_stdin_kwarg(self, stdin_handle: IO[Any] | None) -> IO[Any] | int:
        if self.enabled:
            return subprocess.PIPE
        return stdin_handle if stdin_handle is not None else subprocess.DEVNULL

    def start(
        self,
        tg: anyio.abc.TaskGroup,
        process: subprocess.Popen[Any],
        *,
        capture_file: IO[bytes],
        trigger: anyio.Event,
    ) -> None:
        """Bind the child's piped stdin/stdout and start the tee.

        Called once the task group has been entered, alongside the other
        watchers — the pipes are captured lazily here (not eagerly right
        after spawn) since they don't change between spawn and this point.
        """
        if not self.enabled:
            return
        assert process.stdout is not None
        assert process.stdin is not None
        self._stdout_pipe = process.stdout
        self._stdin_pipe = process.stdin
        tg.start_soon(self._run, capture_file, trigger)

    async def _run(self, capture_file: IO[bytes], trigger: anyio.Event) -> None:
        assert self._line_driver is not None
        assert self._stdout_pipe is not None
        assert self._stdin_pipe is not None

        def _record_failure(diagnostic: str) -> None:
            def _apply() -> None:
                self.failure_ref[0] = diagnostic
                trigger.set()

            anyio.from_thread.run_sync(_apply)

        await anyio.to_thread.run_sync(
            functools.partial(
                drive_process_io,
                stdout_pipe=self._stdout_pipe,
                stdin_pipe=self._stdin_pipe,
                capture_file=capture_file,
                driver=self._line_driver,
                on_failure=_record_failure,
                tee_done=self.done,
            ),
            abandon_on_cancel=True,
        )

    @property
    def failed(self) -> bool:
        return self.enabled and self.failure_ref[0] is not None

    async def _await_settled(self, timeout_seconds: float) -> bool:
        return await anyio.to_thread.run_sync(
            functools.partial(self.done.wait, timeout_seconds),
            abandon_on_cancel=False,
        )

    async def finalize(
        self, *, stdout_file: IO[bytes], stderr_file: IO[bytes], timeout_seconds: float = 5.0
    ) -> None:
        """Bound-wait for the tee, close the capture files, then raise if
        either the tee never settled or the driver recorded a failure.

        A ``False`` settle result is never a source of successful-but-
        partial output — the process is already killed/settled by the time
        this runs, so the child's stdout pipe should already be closed or
        close within moments; expiry here is a cleanup failure.
        """
        if not self.enabled:
            return
        settled = await self._await_settled(timeout_seconds)
        stdout_file.close()
        stderr_file.close()
        if not settled:
            raise LineDriverError(
                "line driver tee did not settle within the cleanup budget "
                "after process termination"
            )
        if self.failure_ref[0] is not None:
            raise LineDriverError(self.failure_ref[0])

    async def settle_best_effort(self, timeout_seconds: float = 5.0) -> None:
        """Shielded-exception-path-only best-effort wait; never raises.

        A slow tee must not still be writing into stdout_file once
        create_temp_io's own cleanup closes it during an unrelated
        exception's unwind — but that original exception always takes
        priority over anything discovered here.
        """
        if self.enabled:
            await self._await_settled(timeout_seconds)


__all__ = ["LineDriverSession"]
