"""Temp file I/O utilities for subprocess stdin/stdout/stderr management."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from autoskillit.core import CapturedStream, SpillSpec, get_logger

logger = get_logger(__name__)


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
