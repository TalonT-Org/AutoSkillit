"""RecordingSubprocessRunner — decorator that records headless sessions as scenario cassettes."""

from __future__ import annotations

import asyncio
import atexit
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import SubprocessResult, SubprocessRunner, TerminationReason, get_logger

if TYPE_CHECKING:
    from api_simulator.claude import ScenarioRecorder

logger = get_logger(__name__)


def _extract_env_and_args(cmd: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse ``["env", "K=V", ..., "program", ...]`` into (env_dict, clean_args).

    If *cmd* does not start with ``"env"``, returns ``({}, cmd)``.
    """
    if not cmd or cmd[0] != "env":
        return {}, list(cmd)

    env_dict: dict[str, str] = {}
    i = 1
    while i < len(cmd) and "=" in cmd[i]:
        key, _, val = cmd[i].partition("=")
        env_dict[key] = val
        i += 1
    return env_dict, cmd[i:]


def _extract_model(args: list[str]) -> str:
    """Find ``--model <model>`` in an argument list."""
    try:
        idx = args.index("--model")
        return args[idx + 1]
    except (ValueError, IndexError):
        return ""


class RecordingSubprocessRunner(SubprocessRunner):
    """Wraps a SubprocessRunner, records each session via ScenarioRecorder.

    - **Session calls** (``pty_mode=True`` + ``SCENARIO_STEP_NAME`` in cmd env prefix):
      delegates to ``ScenarioRecorder.record_step()`` which spawns the real subprocess
      under PTY capture, then constructs a ``SubprocessResult`` from the cassette.
    - **Non-session calls**: delegates to the inner runner, then records a summary via
      ``recorder.record_non_session_step()`` if ``SCENARIO_STEP_NAME`` is present.
    - **Calls without SCENARIO_STEP_NAME**: passes through to inner runner unrecorded.
    """

    def __init__(
        self,
        recorder: ScenarioRecorder,
        inner: SubprocessRunner | None = None,
    ) -> None:
        self._recorder = recorder
        atexit.register(recorder.finalize)
        if inner is None:
            from autoskillit.execution.process import DefaultSubprocessRunner

            inner = DefaultSubprocessRunner()
        self._inner = inner

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str] | None = None,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: Any | None = None,
    ) -> SubprocessResult:
        env_dict, clean_args = _extract_env_and_args(cmd)
        step_name = env_dict.get("SCENARIO_STEP_NAME", "")

        if pty_mode and step_name:
            return await self._record_session(
                cmd=cmd,
                clean_args=clean_args,
                step_name=step_name,
                model=_extract_model(clean_args),
                session_log_dir=session_log_dir,
            )

        # Non-session or no step_name — delegate to inner runner
        result = await self._inner(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            stale_threshold=stale_threshold,
            completion_marker=completion_marker,
            session_log_dir=session_log_dir,
            pty_mode=pty_mode,
            input_data=input_data,
            completion_drain_timeout=completion_drain_timeout,
            linux_tracing_config=linux_tracing_config,
        )

        if step_name:
            tool = env_dict.get("SCENARIO_TOOL", "run_cmd")
            self._recorder.record_non_session_step(
                step_name=step_name,
                tool=tool,
                result_summary={
                    "exit_code": result.returncode,
                    "stdout_head": result.stdout[:500],
                },
            )

        return result

    async def _record_session(
        self,
        *,
        cmd: list[str],
        clean_args: list[str],
        step_name: str,
        model: str,
        session_log_dir: Path | None,
    ) -> SubprocessResult:
        """Record a session call via ScenarioRecorder.record_step()."""
        step_result = await asyncio.to_thread(
            self._recorder.record_step,
            step_name=step_name,
            tool="run_skill",
            args=clean_args,
            model=model,
            session_log_dir=str(session_log_dir) if session_log_dir else None,
        )

        # Read stdout from cassette for _build_skill_result compatibility
        stdout = ""
        cassette_stdout = Path(step_result.cassette_path) / "stdout.jsonl"
        if cassette_stdout.exists():
            stdout = cassette_stdout.read_text()

        return SubprocessResult(
            returncode=step_result.cassette_exit_code,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
            elapsed_seconds=step_result.cassette_duration_ms / 1000.0,
        )
