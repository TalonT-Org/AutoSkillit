"""Temp file I/O utilities for subprocess stdin/stdout/stderr management."""

from __future__ import annotations

import hashlib
import io
import tempfile
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, cast

from autoskillit.core import CapturedStream, LineDriver, SpillSpec, get_logger

logger = get_logger(__name__)

_TEE_CHUNK_SIZE = 65536


class CaptureSetupError(OSError):
    """Raised when the capture directory cannot be created."""


class CaptureReadError(OSError):
    """Raised when a capture file cannot be read after execution."""


@contextmanager
def create_temp_io(
    input_data: str | None = None,
    capture_dir: Path | None = None,
    keep_streams: bool = False,
) -> Generator[tuple[IO[bytes], IO[bytes], Path | None], None, None]:
    """Context manager yielding temp file paths for subprocess I/O.

    Creates temp files for stdout and stderr (and optionally stdin).
    Cleans up on exit regardless of success/failure.

    When *capture_dir* is provided, stdout/stderr files are created inside that
    directory (which is created with ``parents=True`` if needed) and
    *keep_streams* defaults to ``True`` semantics — the stream files are NOT
    deleted on exit (stdin is always deleted).

    Yields:
        Tuple of (stdout_file, stderr_file, stdin_path_or_None) where
        stdout_file and stderr_file are open file handles ready to pass
        to subprocess, and stdin_path is a Path if input_data was provided.
    """
    stdout_file: IO[bytes] | None = None
    stderr_file: IO[bytes] | None = None
    stdin_path: Path | None = None
    paths_to_clean: list[Path] = []

    try:
        if capture_dir is not None:
            try:
                capture_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise CaptureSetupError(
                    f"Cannot create capture directory {capture_dir}: {exc}"
                ) from exc

        _dir = str(capture_dir) if capture_dir is not None else None
        try:
            stdout_file = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="proc_stdout_",
                suffix=".tmp",
                delete=False,
                dir=_dir,
            )
        except OSError as exc:
            raise CaptureSetupError(f"Cannot create stdout temp file in {_dir}: {exc}") from exc
        if not keep_streams and capture_dir is None:
            paths_to_clean.append(Path(stdout_file.name))

        try:
            stderr_file = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="proc_stderr_",
                suffix=".tmp",
                delete=False,
                dir=_dir,
            )
        except OSError as exc:
            stdout_file.close()
            Path(stdout_file.name).unlink(missing_ok=True)
            raise CaptureSetupError(f"Cannot create stderr temp file in {_dir}: {exc}") from exc
        if not keep_streams and capture_dir is None:
            paths_to_clean.append(Path(stderr_file.name))

        if input_data is not None:
            stdin_file = tempfile.NamedTemporaryFile(
                mode="w", prefix="proc_stdin_", suffix=".tmp", delete=False
            )
            stdin_file.write(input_data)
            stdin_file.flush()
            stdin_file.close()
            stdin_path = Path(stdin_file.name)
            paths_to_clean.append(stdin_path)

        yield stdout_file, stderr_file, stdin_path

    finally:
        for f in (stdout_file, stderr_file):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass

        for p in paths_to_clean:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def drive_process_io(
    *,
    stdout_pipe: IO[bytes],
    stdin_pipe: IO[bytes],
    capture_file: IO[bytes],
    driver: LineDriver,
    on_failure: Callable[[str], None],
    tee_done: threading.Event,
) -> None:
    """Synchronous byte tee driving one ``LineDriver`` over a piped child.

    Runs inside a worker thread (``anyio.to_thread.run_sync``). Every raw
    chunk read from ``stdout_pipe`` is appended to ``capture_file`` and
    flushed immediately — before any decoding — preserving the same
    byte-exact, file-backed capture invariant every other transport relies
    on, and unaffected by ``errors="replace"`` decoding used only for the
    driver's own view of the stream. Complete newline-terminated frames are
    decoded and passed to ``driver.on_line`` without their newline
    terminator; every line the driver returns is written back to
    ``stdin_pipe`` (one trailing newline each) and flushed *before* the
    driver's ``failure`` is checked, so an unsupported-request error
    response is never lost even when it is also the line that ends the
    session.

    On failure, stdin is closed and ``on_failure`` is called exactly once
    with the driver's diagnostic — the caller is responsible for bridging
    that call back onto the event loop (this function is anyio-agnostic).
    Draining then continues, capture-only, through EOF: failure is
    signalled the moment it is known rather than waiting for the child to
    exit. On clean completion (``driver.finished``) stdin is likewise
    closed and the remainder of stdout is drained the same way. A trailing
    partial frame at EOF is left exactly as captured — never completed
    with a synthetic newline or presented as a parsed event — and EOF
    reached before the driver finished or failed is itself a failure
    (the app-server exited without completing the handshake).

    A read/write failure on the piped stdin/stdout is folded into
    ``on_failure`` like any other driver-progress failure. A failure
    writing the capture file itself is a different class of fault — an
    infrastructure problem, not a protocol one — and is deliberately left
    to propagate uncaught (the ``finally`` below still signals
    ``tee_done`` first) so the caller's existing unhandled-exception
    cleanup path surfaces it as incomplete evidence rather than folding it
    into a driver diagnostic.

    Never calls ``proc.wait()``, ``proc.kill()``, or closes
    ``capture_file`` — those remain the enclosing runner's responsibility
    once ``tee_done`` is set. EOF alone does not prove the process exited.
    """
    buffer = bytearray()
    stdin_closed = False
    failure_reported = False
    decoding_done = False  # set once failed or finished: capture-only from here

    def _close_stdin() -> None:
        nonlocal stdin_closed
        if not stdin_closed:
            stdin_closed = True
            try:
                stdin_pipe.close()
            except OSError:
                pass

    def _write_lines(lines: tuple[str, ...]) -> None:
        if not lines:
            return
        for outgoing in lines:
            stdin_pipe.write(outgoing.encode("utf-8") + b"\n")
        stdin_pipe.flush()

    def _fail(diagnostic: str) -> None:
        nonlocal failure_reported, decoding_done
        decoding_done = True
        if failure_reported:
            return
        failure_reported = True
        _close_stdin()
        on_failure(diagnostic)

    try:
        try:
            _write_lines(driver.initial_lines())
        except Exception as exc:  # noqa: BLE001 — any driver/pipe fault here is a failure
            logger.warning("line_driver_initial_lines_failed", exc_info=True)
            _fail(f"line driver failed building the initial request: {exc}")

        while True:
            try:
                # read1(), not read(): BufferedReader.read(n) on a non-interactive
                # stream (a pipe is never a tty) keeps issuing raw reads until it
                # has n bytes or hits EOF — it will NOT return early just because
                # data is already sitting in the pipe. A long-lived process that
                # writes small, irregular frames (one JSON-RPC message at a time)
                # almost never fills a 64KiB chunk, so read() blocks the tee for
                # the life of the process, starving on_line()/the driver of every
                # frame until EOF (process exit) unblocks it — by which point any
                # buffered response is decoded too late to write a reply back
                # (the child's stdin is already gone, so the write fails with
                # EPIPE). read1() makes at most one underlying read() syscall and
                # returns whatever is immediately available, exactly like a raw
                # read() on the file descriptor would. read1() is a BufferedIOBase
                # method, not part of the generic IO[bytes] protocol this parameter
                # is typed with — cast() reflects what subprocess.Popen(...).stdout
                # (the only real caller) actually hands us: an io.BufferedReader.
                chunk = cast(io.BufferedReader, stdout_pipe).read1(_TEE_CHUNK_SIZE)
            except OSError as exc:
                _fail(f"stdout pipe read failed: {exc}")
                break
            if not chunk:
                if not decoding_done and not driver.finished:
                    _fail("app-server closed stdout before the handshake completed")
                break

            capture_file.write(chunk)
            capture_file.flush()

            if decoding_done:
                continue

            buffer.extend(chunk)
            while not decoding_done:
                newline_index = buffer.find(b"\n")
                if newline_index == -1:
                    break
                frame = bytes(buffer[:newline_index])
                del buffer[: newline_index + 1]
                decoded_line = frame.decode("utf-8", errors="replace")
                try:
                    outgoing = driver.on_line(decoded_line)
                except Exception as exc:  # noqa: BLE001 — a driver bug is a driver failure
                    logger.warning("line_driver_on_line_failed", exc_info=True)
                    _fail(f"line driver raised in on_line: {exc}")
                    break
                try:
                    _write_lines(outgoing)
                except OSError as exc:
                    _fail(f"stdin pipe write failed: {exc}")
                    break
                if driver.failure is not None:
                    _fail(driver.failure)
                    break
                if driver.finished:
                    decoding_done = True
                    _close_stdin()
    finally:
        _close_stdin()
        tee_done.set()


def read_temp_output(stdout_path: Path, stderr_path: Path) -> tuple[str, str]:
    """Read stdout/stderr from temp files. Safe even if children hold FDs.

    Files aren't EOF-gated like pipes, so this works regardless of whether
    child processes still have the file descriptors open.
    """
    stdout = ""
    stderr = ""
    try:
        stdout = stdout_path.read_text(errors="replace")
    except OSError:
        logger.warning("Failed to read stdout temp file: %s", stdout_path)
    try:
        stderr = stderr_path.read_text(errors="replace")
    except OSError:
        logger.warning("Failed to read stderr temp file: %s", stderr_path)
    return stdout, stderr


_HASH_CHUNK = 65536


def summarize_capture(
    path: Path,
    spec: SpillSpec,
    *,
    complete: bool = True,
) -> CapturedStream:
    """Streaming capture summary — bounded slices only, never a full read."""
    try:
        total_bytes = path.stat().st_size
    except OSError as exc:
        raise CaptureReadError(f"Cannot stat capture file {path}: {exc}") from exc

    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        sha256 = h.hexdigest()
    except OSError as exc:
        raise CaptureReadError(f"Cannot hash capture file {path}: {exc}") from exc

    if total_bytes <= spec.inline_max_chars:
        try:
            inline_text = path.read_text(errors="replace")
        except OSError as exc:
            raise CaptureReadError(f"Cannot read capture file {path}: {exc}") from exc
        return CapturedStream(
            path=path,
            total_bytes=total_bytes,
            sha256=sha256,
            inline_text=inline_text,
            head="",
            tail="",
            complete=complete,
        )

    try:
        with path.open("rb") as f:
            head_bytes = f.read(spec.head_chars)
            f.seek(max(0, total_bytes - spec.tail_chars))
            tail_bytes = f.read(spec.tail_chars)
    except OSError as exc:
        raise CaptureReadError(f"Cannot read capture slices from {path}: {exc}") from exc

    return CapturedStream(
        path=path,
        total_bytes=total_bytes,
        sha256=sha256,
        inline_text=None,
        head=head_bytes.decode("utf-8", errors="replace"),
        tail=tail_bytes.decode("utf-8", errors="replace"),
        complete=complete,
    )
