"""Run-cmd spill lifecycle and stream capture helpers."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    CapturedStream,
    SpillSpec,
    SubprocessResult,
    resolve_temp_dir,
    select_child_session_deadline,
    spill_output,
)
from autoskillit.execution import CaptureReadError
from autoskillit.server.tools import _execution_helpers

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


def _spill_spec(tool_ctx: ToolContext) -> SpillSpec:
    budget = tool_ctx.config.output_budget
    return SpillSpec(
        inline_max_chars=budget.inline_max_chars,
        head_chars=budget.head_chars,
        tail_chars=budget.tail_chars,
    )


def run_cmd_artifact_root(tool_ctx: ToolContext, cwd: str) -> Path:
    if cwd and Path(cwd).is_absolute():
        return (
            resolve_temp_dir(Path(cwd).resolve(), tool_ctx.config.workspace.temp_dir) / "run_cmd"
        )
    return tool_ctx.temp_dir / "run_cmd"


def spill_run_cmd_result(
    tool_ctx: ToolContext,
    *,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    stdout_capture: CapturedStream | None = None,
    stderr_capture: CapturedStream | None = None,
    capture_error: str | None = None,
    execution_error: str | None = None,
) -> dict[str, object]:
    if capture_error is not None:
        result: dict[str, object] = {
            "success": False,
            "exit_code": returncode,
            "error": f"capture_failed: {capture_error}",
        }
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _execution_helpers._process_capture_stream(result, stream_name, capture)
        return result

    if stdout_capture is not None or stderr_capture is not None:
        result = {
            "success": returncode == 0 and execution_error is None,
            "exit_code": returncode,
            "stdout": "",
            "stderr": "",
        }
        if execution_error:
            result["error"] = execution_error
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _execution_helpers._process_capture_stream(result, stream_name, capture)
        return result

    artifact_root = run_cmd_artifact_root(tool_ctx, cwd)
    spec = _spill_spec(tool_ctx)
    shaped_stdout = spill_output(stdout, artifact_root, "stdout", spec)
    shaped_stderr = spill_output(stderr, artifact_root, "stderr", spec)
    result = {
        "success": returncode == 0,
        "exit_code": returncode,
        "stdout": shaped_stdout.text,
        "stderr": shaped_stderr.text,
    }
    if shaped_stdout.artifact_path is not None:
        result["stdout_artifact_path"] = shaped_stdout.artifact_path
    if shaped_stderr.artifact_path is not None:
        result["stderr_artifact_path"] = shaped_stderr.artifact_path
    return result


def _process_capture_stream(
    result: dict[str, object],
    stream_name: str,
    capture: CapturedStream,
) -> None:
    if capture.inline_text is not None:
        result[stream_name] = capture.inline_text
        capture.path.unlink(missing_ok=True)
    else:
        promoted_name = f"{stream_name}_{_uuid8()}.log"
        promoted = capture.path.parent / promoted_name
        try:
            os.replace(capture.path, promoted)
        except OSError as exc:
            result["success"] = False
            result["error"] = (
                f"capture_failed: promote {stream_name} artifact "
                f"{capture.path} -> {promoted}: {exc}"
            )
            return
        fd = os.open(str(promoted.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        complete_str = "true" if capture.complete else "false"
        marker = (
            f"\n[spilled {capture.total_bytes} bytes -> {promoted}"
            f" sha256={capture.sha256} complete={complete_str}]\n"
        )
        result[stream_name] = capture.head + marker + capture.tail
        result[f"{stream_name}_artifact_path"] = str(promoted)
        result[f"{stream_name}_total_bytes"] = capture.total_bytes
        result[f"{stream_name}_sha256"] = capture.sha256


def _uuid8() -> str:
    return uuid.uuid4().hex[:8]


def _summarize_streams(
    sub_result: SubprocessResult,
    spec: SpillSpec,
    complete: bool,
) -> tuple[CapturedStream | None, CapturedStream | None, str | None]:
    stdout_capture = None
    stderr_capture = None
    capture_error: str | None = None
    for stream_name in ("stdout", "stderr"):
        stream_path = getattr(sub_result, f"{stream_name}_path")
        if stream_path is not None:
            try:
                cap = _execution_helpers.summarize_capture(stream_path, spec, complete=complete)
                if stream_name == "stdout":
                    stdout_capture = cap
                else:
                    stderr_capture = cap
            except CaptureReadError as exc:
                capture_error = f"{exc} [orphan={stream_path}]"
                try:
                    stream_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return stdout_capture, stderr_capture, capture_error


def propagate_session_deadline(
    local_deadline: float,
    provider_extras: dict[str, str] | None,
) -> dict[str, str]:
    """Select a child-only deadline without mutating the server environment."""
    extras = provider_extras if provider_extras is not None else {}
    extras["AUTOSKILLIT_SESSION_DEADLINE"] = select_child_session_deadline(
        local_deadline,
        os.environ.get("AUTOSKILLIT_SESSION_DEADLINE", ""),
    )
    return extras
