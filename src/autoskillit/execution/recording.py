"""RecordingSubprocessRunner — decorator that records headless sessions as scenario cassettes."""

from __future__ import annotations

import asyncio
import atexit
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import SubprocessResult, SubprocessRunner, TerminationReason, get_logger

if TYPE_CHECKING:
    from api_simulator.claude import ScenarioRecorder

logger = get_logger(__name__)

#: Environment variable names for scenario recording activation.
RECORD_SCENARIO_ENV = "RECORD_SCENARIO"
RECORD_SCENARIO_DIR_ENV = "RECORD_SCENARIO_DIR"
RECORD_SCENARIO_RECIPE_ENV = "RECORD_SCENARIO_RECIPE"
SCENARIO_STEP_NAME_ENV = "SCENARIO_STEP_NAME"

#: Environment variable names for scenario replay activation.
REPLAY_SCENARIO_ENV = "REPLAY_SCENARIO"
REPLAY_SCENARIO_DIR_ENV = "REPLAY_SCENARIO_DIR"


class ScenarioReplayError(Exception):
    """Raised when scenario replay cannot find a session or result for a step."""


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
        step_name = env_dict.get(SCENARIO_STEP_NAME_ENV, "")

        if pty_mode and step_name:
            return await self._record_session(
                cmd=cmd,
                clean_args=clean_args,
                step_name=step_name,
                model=_extract_model(clean_args),
                session_log_dir=session_log_dir,
            )

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
            self._recorder.record_non_session_step(
                step_name=step_name,
                tool="run_cmd",
                result_summary={
                    "exit_code": result.returncode,
                    "stdout_head": (result.stdout or "")[:500],
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
        try:
            step_result = await asyncio.to_thread(
                self._recorder.record_step,
                step_name=step_name,
                tool="run_skill",
                args=clean_args,
                model=model,
                session_log_dir=str(session_log_dir) if session_log_dir else None,
            )
        except Exception:
            logger.exception("record_step failed for step=%r", step_name)
            raise

        stdout = ""
        if step_result.cassette_path:
            cassette_stdout = Path(step_result.cassette_path) / "stdout.jsonl"
            if cassette_stdout.exists():
                stdout = cassette_stdout.read_text(encoding="utf-8")

        return SubprocessResult(
            returncode=step_result.cassette_exit_code,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
            elapsed_seconds=(step_result.cassette_duration_ms or 0) / 1000.0,
        )


class SequencingSubprocessRunner(SubprocessRunner):
    """Replays pre-recorded sessions by step name.

    Consumes the session map from ``ScenarioPlayer.build_session_map()``
    and non-session step results from the scenario manifest. On each call,
    extracts ``SCENARIO_STEP_NAME`` from the command env prefix and
    dispatches to the matching step queue.
    """

    def __init__(
        self,
        session_map: dict[str, deque[tuple[Any, Any]]],
        non_session_results: dict[str, dict[str, Any]],
    ) -> None:
        self._sessions = session_map
        self._non_session = non_session_results
        self.call_log: list[tuple[str, list[str]]] = []

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
        step_name = env_dict.get(SCENARIO_STEP_NAME_ENV, "")
        self.call_log.append((step_name, cmd))

        if not step_name:
            raise ValueError(f"SCENARIO_STEP_NAME not found in cmd env prefix: {cmd!r}")

        if step_name in self._sessions and self._sessions[step_name]:
            cli, meta = self._sessions[step_name].popleft()
            result = cli.run()
            return SubprocessResult(
                returncode=meta.exit_code,
                stdout=result.stdout,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                elapsed_seconds=meta.duration_ms / 1000.0,
            )

        if step_name in self._non_session:
            summary = self._non_session[step_name]
            return SubprocessResult(
                returncode=summary.get("exit_code", 0),
                stdout=summary.get("stdout_head", ""),
                stderr=summary.get("stderr", ""),
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            )

        raise ScenarioReplayError(
            f"No session or result for step {step_name!r}. "
            f"Available sessions: {sorted(self._sessions.keys())}. "
            f"Available non-session: {sorted(self._non_session.keys())}. "
            f"Register a fallback via player.add_fallback({step_name!r}, cli)."
        )
