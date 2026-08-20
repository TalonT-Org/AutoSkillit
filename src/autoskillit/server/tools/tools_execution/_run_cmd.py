"""MCP tool handler: run_cmd."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

import regex as re
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import TerminationReason, get_logger
from autoskillit.execution import CaptureSetupError, build_sanitized_env
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _check_recipe_read_prohibition,
    _check_write_target_boundary,
    _require_enabled,
    _require_orchestrator_or_higher,
)
from autoskillit.server._misc import SCENARIO_STEP_NAME_ENV
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_segment_delivery import attach_recipe_segment
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    _spill_spec,
    _summarize_streams,
    run_cmd_artifact_root,
    spill_run_cmd_result,
)
from autoskillit.server.tools._execution_helpers import (
    derive_run_cmd_write_prefixes as _derive_run_cmd_write_prefixes,
)

if TYPE_CHECKING:
    from autoskillit.server._recipe_segment_delivery import PreparedRecipeSegmentDelivery

logger = get_logger(__name__)

_PURE_SLEEP_RE = re.compile(
    r'^(?:python3?\s+-c\s+["\']import time;\s*time\.sleep\((?P<py_secs>\d+(?:\.\d+)?)\)["\']'
    r"|sleep\s+(?P<sh_secs>\d+(?:\.\d+)?))$"
)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_cmd")
@track_response_size("run_cmd")
async def run_cmd(
    cmd: str,
    cwd: str,
    timeout: int = 600,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_cmd")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(cmd=cmd)) is not None:
        return gate
    if (
        gate := _check_write_target_boundary(cmd, cwd, _derive_run_cmd_write_prefixes())
    ) is not None:
        return gate
    try:
        prepared_segment: PreparedRecipeSegmentDelivery | None = None
        with structlog.contextvars.bound_contextvars(tool="run_cmd", cwd=cwd):
            if not _derive_run_cmd_write_prefixes():
                logger.debug(
                    "run_cmd: no write prefixes configured — write boundary guard inactive"
                )
            logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
            await _te_pkg._notify(
                ctx, "info", f"run_cmd: {cmd[:80]}", "autoskillit.run_cmd", extra={"cwd": cwd}
            )

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            prepared_segment = _te_pkg.prepare_recipe_segment_delivery(tool_ctx, step_name)
            _start = time.monotonic()
            try:
                m = _PURE_SLEEP_RE.match(cmd.strip())
                if m:
                    seconds = float(m.group("py_secs") or m.group("sh_secs"))
                    await asyncio.sleep(seconds)
                    return json.dumps(
                        {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
                    )
                _env = build_sanitized_env()
                if step_name:
                    _env[SCENARIO_STEP_NAME_ENV] = step_name
                artifact_root = run_cmd_artifact_root(tool_ctx, cwd)
                _timeout_f = float(timeout)
                try:
                    sub_result = await _te_pkg._run_subprocess_captured(
                        ["bash", "-c", cmd],
                        cwd=cwd,
                        timeout=_timeout_f,
                        env=_env,
                        capture_dir=artifact_root,
                    )
                except CaptureSetupError as exc:
                    result = spill_run_cmd_result(
                        tool_ctx,
                        cwd=cwd,
                        returncode=-1,
                        stdout="",
                        stderr="",
                        capture_error=str(exc),
                    )
                    return json.dumps(
                        attach_recipe_segment(
                            result,
                            prepared_segment,
                            success=False,
                        )
                    )

                spec = _spill_spec(tool_ctx)
                returncode = sub_result.returncode
                execution_error: str | None = None
                complete = True

                term = sub_result.termination
                if term == TerminationReason.NATURAL_EXIT:
                    returncode = sub_result.returncode
                elif term == TerminationReason.TIMED_OUT:
                    returncode = -1
                    execution_error = f"Process timed out after {_timeout_f}s"
                    complete = False
                elif term == TerminationReason.SIGNAL_DEATH:
                    execution_error = (
                        f"Process died to signal (returncode={sub_result.returncode})"
                    )
                    complete = False
                else:
                    execution_error = f"Unexpected termination: {term.value}"
                    complete = False

                stdout_capture, stderr_capture, capture_error = _summarize_streams(
                    sub_result, spec, complete
                )

                result = spill_run_cmd_result(
                    tool_ctx,
                    cwd=cwd,
                    returncode=returncode,
                    stdout="",
                    stderr="",
                    stdout_capture=stdout_capture,
                    stderr_capture=stderr_capture,
                    capture_error=capture_error,
                    execution_error=execution_error,
                )
                if not result.get("success"):
                    await _te_pkg._notify(
                        ctx,
                        "error",
                        "run_cmd failed",
                        "autoskillit.run_cmd",
                        extra={"exit_code": returncode},
                    )
                if result.get("success") is True:
                    return json.dumps(result)
                return json.dumps(attach_recipe_segment(result, prepared_segment, success=False))
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("run_cmd unhandled exception", exc_info=True)
        return json.dumps(
            attach_recipe_segment(
                {
                    "success": False,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"{type(exc).__name__}: {exc}",
                },
                prepared_segment,
                success=False,
            )
        )
