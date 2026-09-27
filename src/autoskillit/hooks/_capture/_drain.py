"""Read a capture pipe to EOF and mint evidence for the completed stream."""

from __future__ import annotations

import os
import selectors
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.hooks import _capture_process
    from autoskillit.hooks._capture._authority import CaptureSetupError
    from autoskillit.hooks._capture._failure_policy import CaptureFailureReason
    from autoskillit.hooks._capture._module_identity import register_module_aliases
    from autoskillit.hooks._capture._snapshot import CaptureMeasurement
elif __package__ == "_capture":
    import _capture_process
    from _capture._authority import CaptureSetupError
    from _capture._failure_policy import CaptureFailureReason
    from _capture._module_identity import register_module_aliases
    from _capture._snapshot import CaptureMeasurement
else:
    from .. import _capture_process
    from ._authority import CaptureSetupError
    from ._failure_policy import CaptureFailureReason
    from ._module_identity import register_module_aliases
    from ._snapshot import CaptureMeasurement

register_module_aliases(__name__)

_DRAIN_CHUNK_BYTES = 64 * 1024
_DRAIN_POLL_SECONDS = 0.05
_PIPE_EOF_TOKEN = object()


@dataclass(frozen=True, slots=True)
class PipeEofEvidence:
    measurement: CaptureMeasurement
    write_error: OSError | None
    _token: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._token is not _PIPE_EOF_TOKEN:
            raise TypeError("PipeEofEvidence instances must come from drain_capture")


def drain_capture(
    process: subprocess.Popen[bytes] | _capture_process.OwnedProcessGroup,
    artifact_writer_fd: int,
    inline_bytes: int,
    *,
    digest_factory: Callable[[], Any],
    write_all: Callable[[int, bytes], None],
) -> PipeEofEvidence:
    """Read the combined subprocess pipe and persist bounded replay metadata."""

    stream = process.stdout
    if stream is None:
        raise CaptureSetupError.filesystem_io("capture pipe unavailable")

    head_limit = (2 * inline_bytes) // 3
    tail_limit = inline_bytes - head_limit
    total = 0
    digest = digest_factory()
    inline = bytearray()
    head = bytearray()
    tail = bytearray()
    write_error: OSError | None = None

    def consume(chunk: bytes) -> None:
        nonlocal total, write_error
        total += len(chunk)
        digest.update(chunk)
        if write_error is None:
            try:
                write_all(artifact_writer_fd, chunk)
            except OSError as exc:
                write_error = exc
        inline.extend(chunk[: max(0, inline_bytes + 1 - len(inline))])
        head.extend(chunk[: max(0, head_limit - len(head))])
        tail.extend(chunk)
        del tail[: max(0, len(tail) - tail_limit)]

    def mint_evidence() -> PipeEofEvidence:
        measurement = CaptureMeasurement(
            total_bytes=total,
            sha256=digest.hexdigest(),
            inline_bytes=inline_bytes,
            inline=bytes(inline),
            head=bytes(head),
            tail=bytes(tail),
        )
        return _mint(process, measurement, write_error)

    if not isinstance(process, _capture_process.OwnedProcessGroup):
        while True:
            chunk = stream.read(_DRAIN_CHUNK_BYTES)
            if not chunk:
                return mint_evidence()
            consume(chunk)

    _drain_owned_pipe(process, stream.fileno(), consume)
    return mint_evidence()


def _drain_owned_pipe(
    owner: _capture_process.OwnedProcessGroup,
    descriptor: int,
    consume: Callable[[bytes], None],
) -> None:
    os.set_blocking(descriptor, False)
    selector_factory = selectors.DefaultSelector
    selector = selector_factory()
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        while True:
            for _key, _events in selector.select(_DRAIN_POLL_SECONDS):
                while True:
                    try:
                        chunk = os.read(descriptor, _DRAIN_CHUNK_BYTES)
                    except BlockingIOError:
                        break
                    if not chunk:
                        selector.unregister(descriptor)
                        return
                    consume(chunk)

            owner.poll()
    finally:
        selector.close()


def _mint(
    process: subprocess.Popen[bytes] | _capture_process.OwnedProcessGroup,
    measurement: CaptureMeasurement,
    write_error: OSError | None,
) -> PipeEofEvidence:
    if isinstance(process, _capture_process.OwnedProcessGroup) and process.runner_signalled:
        raise CaptureSetupError(
            CaptureFailureReason.RUNNER_SETTLEMENT,
            "runner signal preceded pipe EOF",
        )
    return PipeEofEvidence(measurement, write_error, _token=_PIPE_EOF_TOKEN)


__all__ = ["PipeEofEvidence", "drain_capture"]
