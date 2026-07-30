"""Checked hook-output replay and runner-settlement primitives."""

from __future__ import annotations

import errno
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from autoskillit.hooks._capture._snapshot import (
        FinalizedCapture,
        PublishedCaptureReference,
        UnavailableCaptureReference,
    )
    from autoskillit.hooks._capture_contract import (
        CaptureFailureV2,
        render_capture_failure_v2,
        render_capture_v2,
    )
else:
    from ._snapshot import (
        FinalizedCapture,
        PublishedCaptureReference,
        UnavailableCaptureReference,
    )

    if __package__ == "_capture":
        from _capture_contract import (
            CaptureFailureV2,
            render_capture_failure_v2,
            render_capture_v2,
        )
    else:
        from .._capture_contract import (
            CaptureFailureV2,
            render_capture_failure_v2,
            render_capture_v2,
        )

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._replay", "autoskillit.hooks._capture._replay"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture replay module identity")

_CAPTURE_FAILURE_RETURN_CODE = 1
_PROCESS_SETTLE_TIMEOUT_SECONDS = 2
_RUNTIME_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
    TypeError,
    UnicodeError,
    ValueError,
)


class WritableBinaryStream(Protocol):
    def write(self, value: memoryview) -> int | None: ...


class _ReplayError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerSettlementEvidence:
    action: str
    returncode: int | None


def write_all_stream(
    stream: WritableBinaryStream,
    payload: bytes,
    *,
    boundary: str,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    view = memoryview(payload)
    while view:
        written = stream.write(view)
        if (
            not isinstance(written, int)
            or isinstance(written, bool)
            or written <= 0
            or written > len(view)
        ):
            raise OSError(errno.EIO, f"{boundary} write made no progress")
        if on_progress is not None:
            on_progress(written)
        view = view[written:]


def write_and_flush_hook_stdout(
    payload: bytes,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    write_all_stream(
        sys.stdout.buffer,
        payload,
        boundary="hook stdout",
        on_progress=on_progress,
    )
    sys.stdout.buffer.flush()


def _write_and_flush_hook_stderr(payload: bytes) -> None:
    write_all_stream(sys.stderr.buffer, payload, boundary="hook stderr")
    sys.stderr.buffer.flush()


def _failure_stage(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in value.lower()
    ).strip("_")[:64]
    return normalized if normalized and normalized[0].isalpha() else "capture_failure"


def _bounded_detail(value: str) -> str:
    normalized = " ".join(value.split()) or "capture failure"
    return normalized.encode("utf-8")[:240].decode("utf-8", errors="ignore")


def failure_transport(
    *,
    stage: str,
    detail: str,
    shell_returncode: int | None,
    settlement: RunnerSettlementEvidence | None,
) -> CaptureFailureV2:
    return CaptureFailureV2(
        stage=_failure_stage(stage),
        detail=_bounded_detail(detail),
        shell_returncode=shell_returncode,
        settlement_returncode=None if settlement is None else settlement.returncode,
    )


def runner_failure(stage: str, detail: str) -> CaptureFailureV2:
    return failure_transport(
        stage=stage,
        detail=detail,
        shell_returncode=None,
        settlement=None,
    )


def _emit_failure(failure: CaptureFailureV2) -> None:
    _write_and_flush_hook_stderr(render_capture_failure_v2(failure) + b"\n")


def capture_failure_return(failure: CaptureFailureV2) -> int:
    returncode = failure.shell_returncode
    result = _CAPTURE_FAILURE_RETURN_CODE if returncode is None or returncode == 0 else returncode
    try:
        _emit_failure(failure)
    except _RUNTIME_ERRORS:
        pass
    return result


def render_inline_capture(finalized: FinalizedCapture) -> bytes:
    if type(finalized) is not FinalizedCapture or finalized.issuance is not None:
        raise _ReplayError("inline replay requires unreferenced finalized capture")
    snapshot = finalized.snapshot
    if len(snapshot.measurement.inline) != snapshot.manifest.total_bytes:
        raise _ReplayError("inline replay does not cover verified snapshot")
    return snapshot.measurement.inline


def render_oversized_capture(
    value: PublishedCaptureReference | UnavailableCaptureReference,
) -> bytes:
    if type(value) not in {
        PublishedCaptureReference,
        UnavailableCaptureReference,
    }:
        raise _ReplayError("oversized replay requires a publication result")
    snapshot = value.snapshot
    return (
        snapshot.measurement.head
        + b"\n"
        + render_capture_v2(value)
        + b"\n"
        + snapshot.measurement.tail
    )


def settle_failed_capture(
    process: subprocess.Popen[bytes],
) -> RunnerSettlementEvidence:
    if process.stdout is not None:
        try:
            process.stdout.close()
        except _RUNTIME_ERRORS:
            pass
    try:
        running = process.poll() is None
    except _RUNTIME_ERRORS:
        running = True
    action = "already_exited"
    if running:
        try:
            process.terminate()
            action = "terminated"
        except _RUNTIME_ERRORS:
            try:
                process.kill()
                action = "killed"
            except _RUNTIME_ERRORS:
                action = "unknown"
    try:
        return RunnerSettlementEvidence(
            action=action,
            returncode=process.wait(timeout=_PROCESS_SETTLE_TIMEOUT_SECONDS),
        )
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            action = "killed"
        except _RUNTIME_ERRORS:
            action = "unknown"
        try:
            return RunnerSettlementEvidence(
                action=action,
                returncode=process.wait(timeout=_PROCESS_SETTLE_TIMEOUT_SECONDS),
            )
        except _RUNTIME_ERRORS:
            return RunnerSettlementEvidence(action=action, returncode=None)
    except _RUNTIME_ERRORS:
        return RunnerSettlementEvidence(action=action, returncode=None)
