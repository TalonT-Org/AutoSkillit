"""RecordingSubprocessRunner and ReplayingSubprocessRunner — scenario I/O for headless sessions.

See docs/design/recording-replay-accepted-degradations.md for the preserved
backend-specific cassette formats and the managed Claude PTY recording boundary.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    DEFAULT_CLEANUP_BUDGET_SECONDS,
    BackendCapabilities,
    CleanupOutcome,
    InspectorCallback,
    KillReason,
    ProcessIdentity,
    SubprocessResult,
    SubprocessRunner,
    TerminationReason,
    ValidatedAddDir,
    atomic_write,
    fast_dumps,
    fast_loads,
    get_logger,
    read_starttime_ticks,
)
from autoskillit.execution._recording_skills import (
    _extract_ephemeral_add_dir,
    scan_skill_snapshots,
    snapshot_skill_dir,
)
from autoskillit.execution._recording_skills import (
    restore_skill_snapshot as _restore_skill_snapshot,
)
from autoskillit.execution.backends.codex_scenario_player import CodexScenarioPlayer

if TYPE_CHECKING:
    from api_simulator.claude import ScenarioPlayer, ScenarioRecorder

    from autoskillit.core import StreamParserFactory

logger = get_logger(__name__)

_RECORDING_SUPERVISOR_BOOTSTRAP = (
    "from autoskillit.execution.recording import _recording_supervisor_entrypoint; "
    "raise SystemExit(_recording_supervisor_entrypoint())"
)


def _no_owned_process_cleanup() -> CleanupOutcome:
    return CleanupOutcome(succeeded=True, budget_exhausted=False)


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, (bool, int, float, str)):
        with suppress(ValueError, TypeError, OverflowError):
            return int(value)
    return default


def _float_value(value: object, default: float = 0.0) -> float:
    if isinstance(value, (bool, int, float, str)):
        with suppress(ValueError, TypeError, OverflowError):
            return float(value)
    return default


def _identity_payload(identity: ProcessIdentity) -> dict[str, object]:
    return {
        "root_pid": identity.root_pid,
        "starttime_ticks": identity.starttime_ticks,
        "fallback_create_time": identity.fallback_create_time,
        "process_group_id": identity.process_group_id,
        "session_id": identity.session_id,
        "descendants": [list(item) for item in identity.descendants],
    }


def _identity_from_payload(payload: Mapping[str, object]) -> ProcessIdentity:
    raw_descendants = payload.get("descendants", ())
    if not isinstance(raw_descendants, (list, tuple)):
        raw_descendants = ()
    descendants = tuple(
        (_int_value(item[0]), _int_value(item[1]))
        for item in raw_descendants
        if isinstance(item, (list, tuple)) and len(item) == 2
    )
    return ProcessIdentity(
        root_pid=_int_value(payload.get("root_pid", 0)),
        starttime_ticks=_int_value(payload.get("starttime_ticks", 0)),
        fallback_create_time=_float_value(payload.get("fallback_create_time", 0.0)),
        process_group_id=_int_value(payload.get("process_group_id", 0)),
        session_id=_int_value(payload.get("session_id", 0)),
        descendants=descendants,
    )


def _cleanup_payload(outcome: CleanupOutcome) -> dict[str, object]:
    return {
        "succeeded": outcome.succeeded,
        "budget_exhausted": outcome.budget_exhausted,
        "retained_identities": [
            _identity_payload(identity) for identity in outcome.retained_identities
        ],
        "unknown_identities": [
            _identity_payload(identity) for identity in outcome.unknown_identities
        ],
    }


def _cleanup_from_payload(payload: Mapping[str, object]) -> CleanupOutcome:
    def _identities(name: str) -> tuple[ProcessIdentity, ...]:
        raw = payload.get(name, ())
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(_identity_from_payload(item) for item in raw if isinstance(item, Mapping))

    return CleanupOutcome(
        succeeded=bool(payload.get("succeeded", False)),
        budget_exhausted=bool(payload.get("budget_exhausted", False)),
        retained_identities=_identities("retained_identities"),
        unknown_identities=_identities("unknown_identities"),
    )


def _write_recording_receipt(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write(path, fast_dumps(dict(payload), indent=True))


def _read_recording_receipt(path: Path) -> dict[str, object] | None:
    try:
        payload = fast_loads(path.read_bytes())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


# Environment variable names for scenario recording activation.
RECORD_SCENARIO_ENV = "RECORD_SCENARIO"
RECORD_SCENARIO_DIR_ENV = "RECORD_SCENARIO_DIR"
RECORD_SCENARIO_RECIPE_ENV = "RECORD_SCENARIO_RECIPE"
SCENARIO_STEP_NAME_ENV = "SCENARIO_STEP_NAME"

# Environment variable names for scenario replay activation.
REPLAY_SCENARIO_ENV = "REPLAY_SCENARIO"
REPLAY_SCENARIO_DIR_ENV = "REPLAY_SCENARIO_DIR"


def _detect_backend_format(scenario_dir: Path) -> str:
    if any(scenario_dir.glob("*/codex_stdout.ndjson")):
        return "codex"
    return "claude"


class ScenarioReplayError(Exception):
    """Raised when scenario replay cannot find a session or result for a step."""


def _extract_model(args: list[str]) -> str:
    """Find ``--model <model>`` in an argument list."""
    try:
        idx = args.index("--model")
        return args[idx + 1]
    except (ValueError, IndexError):
        return ""


def _current_process_identity() -> ProcessIdentity:
    pid = os.getpid()
    return ProcessIdentity(
        root_pid=pid,
        starttime_ticks=read_starttime_ticks(pid) or 0,
        process_group_id=os.getpgid(pid),
        session_id=os.getsid(pid),
    )


def _supervisor_exit_code(returncode: int) -> int:
    if returncode < 0:
        return min(255, 128 + abs(returncode))
    return min(255, returncode)


def _recording_supervisor_entrypoint() -> int:
    """Run one recorded command with inherited PTY I/O and verified cleanup."""
    if len(sys.argv) != 3:
        return 2
    payload_path = Path(sys.argv[1])
    receipt_path = Path(sys.argv[2])
    cancel_path = receipt_path.with_suffix(".cancel")
    raw_payload = fast_loads(payload_path.read_bytes())
    if not isinstance(raw_payload, dict):
        return 2

    cmd = raw_payload.get("cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(arg, str) for arg in cmd):
        return 2
    cwd = Path(str(raw_payload.get("cwd", ".")))
    timeout = _float_value(raw_payload.get("timeout", 0.0))
    cleanup_budget_seconds = _float_value(
        raw_payload.get("cleanup_budget_seconds", DEFAULT_CLEANUP_BUDGET_SECONDS),
        DEFAULT_CLEANUP_BUDGET_SECONDS,
    )
    raw_env = raw_payload.get("env")
    child_env = (
        {str(key): str(value) for key, value in raw_env.items()}
        if isinstance(raw_env, dict)
        else None
    )

    supervisor_identity = _current_process_identity()
    _write_recording_receipt(
        receipt_path,
        {
            "state": "starting",
            "supervisor_identity": _identity_payload(supervisor_identity),
        },
    )

    from autoskillit.execution.process import (  # noqa: PLC0415
        _finalize_owned_process_sync,
        _TerminationSignalState,
        make_tracker,
    )

    process: subprocess.Popen[bytes] | None = None
    tracker = make_tracker()
    signal_state = _TerminationSignalState()
    cleanup_outcome = _no_owned_process_cleanup()
    termination = TerminationReason.NATURAL_EXIT
    error = ""
    started_at = time.monotonic()
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=child_env,
            start_new_session=True,
        )
        tracker.seed_root(
            process.pid,
            process_group_id=process.pid,
            session_id=process.pid,
        )
        tracker.enrich_root_identity()
        _write_recording_receipt(
            receipt_path,
            {
                "state": "running",
                "supervisor_identity": _identity_payload(supervisor_identity),
                "pid": process.pid,
            },
        )
        process_deadline = time.monotonic() + max(0.0, timeout)
        while process.poll() is None:
            if cancel_path.exists():
                termination = TerminationReason.SIGNAL_DEATH
                error = "supervisor_cancelled"
                break
            remaining = process_deadline - time.monotonic()
            if remaining <= 0:
                termination = TerminationReason.TIMED_OUT
                break
            time.sleep(min(0.05, remaining))
    except BaseException as exc:
        with suppress(BaseException):
            logger.error("recording_supervisor_failed", error=exc, exc_info=True)
        termination = TerminationReason.SIGNAL_DEATH
        error = f"{type(exc).__name__}:{exc}"
    finally:
        if process is not None:
            cleanup_outcome = _finalize_owned_process_sync(
                process=process,
                tracker=tracker,
                budget_seconds=cleanup_budget_seconds,
                signal_state=signal_state,
            )

    returncode = (
        process.returncode if process is not None and process.returncode is not None else 1
    )
    if termination is TerminationReason.TIMED_OUT:
        kill_reason = KillReason.INFRA_KILL
    elif signal_state.signaled or not cleanup_outcome.succeeded:
        kill_reason = KillReason.INFRA_KILL
    else:
        kill_reason = KillReason.NATURAL_EXIT
    _write_recording_receipt(
        receipt_path,
        {
            "state": "complete",
            "supervisor_identity": _identity_payload(supervisor_identity),
            "pid": process.pid if process is not None else 0,
            "returncode": returncode,
            "termination": termination.value,
            "kill_reason": kill_reason.value,
            "elapsed_seconds": time.monotonic() - started_at,
            "cleanup_outcome": _cleanup_payload(cleanup_outcome),
            "error": error,
        },
    )
    return _supervisor_exit_code(returncode)


async def _interrupt_recording_supervisor(
    receipt_path: Path,
    record_task: asyncio.Task[Any],
    cleanup_budget_seconds: float,
) -> None:
    """Request cooperative stop and let the recorder reap the supervisor."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, cleanup_budget_seconds)
    cancel_path = receipt_path.with_suffix(".cancel")
    receipt: dict[str, object] | None = None
    while loop.time() < deadline:
        receipt = _read_recording_receipt(receipt_path)
        if receipt is not None and isinstance(receipt.get("supervisor_identity"), Mapping):
            break
        if record_task.done():
            return
        await asyncio.sleep(0.01)

    if receipt is not None and isinstance(receipt.get("supervisor_identity"), Mapping):
        raw_identity = receipt.get("supervisor_identity")
        assert isinstance(raw_identity, Mapping)
        identity = _identity_from_payload(raw_identity)
        atomic_write(cancel_path, "cancel\n")
        if record_task.done():
            while loop.time() < deadline:
                try:
                    reaped_pid, _status = await asyncio.to_thread(
                        os.waitpid,
                        identity.root_pid,
                        os.WNOHANG,
                    )
                except ChildProcessError:
                    break
                if reaped_pid == identity.root_pid:
                    break
                await asyncio.sleep(0.01)

    remaining = max(0.0, deadline - loop.time())
    if remaining > 0 and not record_task.done():
        with suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(record_task), timeout=remaining)
    with suppress(OSError):
        cancel_path.unlink()


class RecordingSubprocessRunner(SubprocessRunner):
    """Wraps a SubprocessRunner, records each session via ScenarioRecorder.

    Dispatch paths (checked in order):

    1. **PTY session** (``step_name`` + ``pty_mode=True``):
       delegates to ``ScenarioRecorder.record_step()`` which spawns the real subprocess
       under PTY capture, then constructs a ``SubprocessResult`` from the cassette.
    2. **Non-PTY Codex session** (``step_name`` + ``pty_mode=False`` +
       ``capabilities.pty_required=False``): delegates to the inner runner, writes
       ``codex_stdout.ndjson`` and ``step_meta.json`` cassette files, then records via
       ``recorder.record_non_session_step(tool='run_skill')``.
    3. **Non-session command** (``step_name`` + ``pty_mode=False`` +
       ``capabilities.pty_required=True``):
       delegates to the inner runner, then records a summary via
       ``recorder.record_non_session_step(tool='run_cmd')``.
    4. **Untracked** (no ``step_name``): passes through to inner runner unrecorded.

    Public attribute ``recorder`` holds the :class:`ScenarioRecorder` instance.
    The symmetric counterpart :class:`ReplayingSubprocessRunner` exposes ``player``
    (a ``ScenarioPlayer``).  The different names reflect the different domain objects
    each class wraps — the asymmetry is intentional.
    """

    def __init__(
        self,
        recorder: ScenarioRecorder,
        inner: SubprocessRunner | None = None,
        *,
        scenario_dir: Path | None = None,
        capabilities: BackendCapabilities = CLAUDE_CODE_CAPABILITIES,
    ) -> None:
        self.recorder = recorder
        self._scenario_dir = scenario_dir
        self._capabilities = capabilities
        self._backend_name = "claude-code" if capabilities.pty_required else "codex"
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
        env: Mapping[str, str] | None = None,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: Any | None = None,
        idle_output_timeout: float | None = None,
        max_suppression_seconds: float | None = None,
        on_pid_resolved: Callable[[int, int], None] | None = None,
        enable_deadline_extension: bool = False,
        max_extension_seconds: float = 7200,
        marker_dir: Path | None = None,
        marker_scope_session_id: str | None = None,
        stream_parser_factory: StreamParserFactory | None = None,
        parent_candidate_normalizer: Callable[[dict[str, Any], int], Any] | None = None,
        completion_record_types: frozenset[str] = frozenset({"result"}),
        session_record_types: frozenset[str] = frozenset({"assistant"}),
        inspector_callback: InspectorCallback | None = None,
        workload_basenames: frozenset[str] | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
    ) -> SubprocessResult:
        step_name = (env or {}).get(SCENARIO_STEP_NAME_ENV, "")

        if step_name:
            if pty_mode:
                return await self._record_session(
                    cmd=cmd,
                    cwd=cwd,
                    timeout=timeout,
                    env=env,
                    step_name=step_name,
                    model=_extract_model(cmd),
                    session_log_dir=session_log_dir,
                    cleanup_budget_seconds=cleanup_budget_seconds,
                )

            if not self._capabilities.pty_required:
                return await self._record_non_pty_session(
                    cmd=cmd,
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
                    idle_output_timeout=idle_output_timeout,
                    max_suppression_seconds=max_suppression_seconds,
                    on_pid_resolved=on_pid_resolved,
                    enable_deadline_extension=enable_deadline_extension,
                    max_extension_seconds=max_extension_seconds,
                    marker_dir=marker_dir,
                    marker_scope_session_id=marker_scope_session_id,
                    stream_parser_factory=stream_parser_factory,
                    parent_candidate_normalizer=parent_candidate_normalizer,
                    step_name=step_name,
                    completion_record_types=completion_record_types,
                    session_record_types=session_record_types,
                    inspector_callback=inspector_callback,
                    workload_basenames=workload_basenames,
                    on_session_id_resolved=on_session_id_resolved,
                    cleanup_budget_seconds=cleanup_budget_seconds,
                )

            # Non-Codex, non-PTY with step_name: run inner + record summary.
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
                idle_output_timeout=idle_output_timeout,
                max_suppression_seconds=max_suppression_seconds,
                on_pid_resolved=on_pid_resolved,
                enable_deadline_extension=enable_deadline_extension,
                max_extension_seconds=max_extension_seconds,
                marker_dir=marker_dir,
                marker_scope_session_id=marker_scope_session_id,
                stream_parser_factory=stream_parser_factory,
                parent_candidate_normalizer=parent_candidate_normalizer,
                completion_record_types=completion_record_types,
                session_record_types=session_record_types,
                inspector_callback=inspector_callback,
                workload_basenames=workload_basenames,
                on_session_id_resolved=on_session_id_resolved,
                cleanup_budget_seconds=cleanup_budget_seconds,
            )
            self.recorder.record_non_session_step(
                step_name=step_name,
                tool="run_cmd",
                result_summary={
                    "exit_code": result.returncode,
                    "stdout_head": (result.stdout or "")[:500],
                },
            )
            return result

        # No step_name: T3 boundary — pass through to inner runner unrecorded.
        return await self._inner(
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
            idle_output_timeout=idle_output_timeout,
            max_suppression_seconds=max_suppression_seconds,
            on_pid_resolved=on_pid_resolved,
            enable_deadline_extension=enable_deadline_extension,
            max_extension_seconds=max_extension_seconds,
            marker_dir=marker_dir,
            marker_scope_session_id=marker_scope_session_id,
            stream_parser_factory=stream_parser_factory,
            parent_candidate_normalizer=parent_candidate_normalizer,
            completion_record_types=completion_record_types,
            session_record_types=session_record_types,
            inspector_callback=inspector_callback,
            workload_basenames=workload_basenames,
            on_session_id_resolved=on_session_id_resolved,
            cleanup_budget_seconds=cleanup_budget_seconds,
        )

    async def _record_session(
        self,
        *,
        cmd: list[str],
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None,
        step_name: str,
        model: str,
        session_log_dir: Path | None,
        cleanup_budget_seconds: float,
    ) -> SubprocessResult:
        """Record through a PTY while a child supervisor owns finalization."""
        supervisor_dir = (
            self._scenario_dir / ".recording-supervisor"
            if self._scenario_dir is not None
            else cwd / ".autoskillit" / "temp" / "recording-supervisor"
        )
        supervisor_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        payload_path = supervisor_dir / f"{token}.input.json"
        receipt_path = supervisor_dir / f"{token}.receipt.json"
        atomic_write(
            payload_path,
            fast_dumps(
                {
                    "cmd": cmd,
                    "cwd": str(cwd),
                    "timeout": timeout,
                    "env": dict(env) if env is not None else None,
                    "cleanup_budget_seconds": cleanup_budget_seconds,
                },
                indent=True,
            ),
        )
        payload_path.chmod(0o600)
        supervisor_cmd = [
            sys.executable,
            "-c",
            _RECORDING_SUPERVISOR_BOOTSTRAP,
            str(payload_path),
            str(receipt_path),
        ]
        record_task = asyncio.create_task(
            asyncio.to_thread(
                self.recorder.record_step,
                step_name=step_name,
                tool="run_skill",
                args=supervisor_cmd,
                model=model,
                session_log_dir=str(session_log_dir) if session_log_dir else None,
            )
        )
        try:
            step_result = await asyncio.shield(record_task)
        except BaseException:
            cleanup_task = asyncio.create_task(
                _interrupt_recording_supervisor(
                    receipt_path,
                    record_task,
                    cleanup_budget_seconds,
                )
            )
            with suppress(BaseException):
                await asyncio.shield(cleanup_task)
            with suppress(BaseException):
                logger.exception("record_step failed for step=%r", step_name)
            with suppress(OSError):
                receipt_path.unlink()
            raise
        finally:
            with suppress(OSError):
                payload_path.unlink()

        stdout = ""
        if step_result.cassette_path:
            cassette_path = Path(step_result.cassette_path)
            cassette_stdout = cassette_path / "stdout.jsonl"
            if cassette_stdout.exists():
                stdout = cassette_stdout.read_text(encoding="utf-8")
            atomic_write(
                cassette_path / "input.json",
                fast_dumps({"args": cmd}, indent=True),
            )

        ephemeral_dir = _extract_ephemeral_add_dir(cmd)
        if ephemeral_dir is not None and step_result.cassette_path:
            _scenario_dir = (
                self._scenario_dir
                if self._scenario_dir is not None
                else Path(step_result.cassette_path).parent.parent
            )
            _snap = snapshot_skill_dir(_scenario_dir, step_name, ephemeral_dir)
            if _snap:
                logger.debug("skill_dir_snapshot_written", step=step_name, path=str(_snap))

        receipt = _read_recording_receipt(receipt_path)
        with suppress(OSError):
            receipt_path.unlink()
        fake_cleanup = getattr(step_result, "cleanup_outcome", None)
        if receipt is not None and receipt.get("state") == "complete":
            raw_cleanup = receipt.get("cleanup_outcome")
            cleanup_outcome = (
                _cleanup_from_payload(raw_cleanup)
                if isinstance(raw_cleanup, Mapping)
                else CleanupOutcome(succeeded=False, budget_exhausted=False)
            )
            returncode = _int_value(
                receipt.get("returncode", step_result.cassette_exit_code),
                step_result.cassette_exit_code,
            )
            termination = TerminationReason(
                str(receipt.get("termination", TerminationReason.NATURAL_EXIT.value))
            )
            kill_reason = KillReason(
                str(receipt.get("kill_reason", KillReason.NATURAL_EXIT.value))
            )
            pid = _int_value(receipt.get("pid", 0))
            elapsed_seconds = _float_value(
                receipt.get(
                    "elapsed_seconds",
                    (step_result.cassette_duration_ms or 0) / 1000.0,
                ),
                (step_result.cassette_duration_ms or 0) / 1000.0,
            )
        elif isinstance(fake_cleanup, CleanupOutcome):
            cleanup_outcome = fake_cleanup
            returncode = step_result.cassette_exit_code
            termination = getattr(
                step_result,
                "termination",
                TerminationReason.NATURAL_EXIT,
            )
            kill_reason = getattr(step_result, "kill_reason", KillReason.NATURAL_EXIT)
            pid = int(getattr(step_result, "pid", 0))
            elapsed_seconds = (step_result.cassette_duration_ms or 0) / 1000.0
        else:
            cleanup_outcome = CleanupOutcome(succeeded=False, budget_exhausted=False)
            returncode = step_result.cassette_exit_code
            termination = TerminationReason.SIGNAL_DEATH
            kill_reason = KillReason.INFRA_KILL
            pid = 0
            elapsed_seconds = (step_result.cassette_duration_ms or 0) / 1000.0

        return SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            termination=termination,
            pid=pid,
            elapsed_seconds=elapsed_seconds,
            kill_reason=kill_reason,
            cleanup_outcome=cleanup_outcome,
        )

    async def _record_non_pty_session(
        self,
        *,
        cmd: list[str],
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None,
        stale_threshold: float,
        completion_marker: str,
        session_log_dir: Path | None,
        pty_mode: bool,
        input_data: str | None,
        completion_drain_timeout: float,
        linux_tracing_config: Any | None,
        idle_output_timeout: float | None,
        max_suppression_seconds: float | None,
        on_pid_resolved: Callable[[int, int], None] | None,
        enable_deadline_extension: bool,
        max_extension_seconds: float,
        marker_dir: Path | None,
        marker_scope_session_id: str | None,
        stream_parser_factory: StreamParserFactory | None,
        parent_candidate_normalizer: Callable[[dict[str, Any], int], Any] | None,
        step_name: str,
        completion_record_types: frozenset[str] = frozenset({"result"}),
        session_record_types: frozenset[str] = frozenset({"assistant"}),
        inspector_callback: InspectorCallback | None = None,
        workload_basenames: frozenset[str] | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
    ) -> SubprocessResult:
        """Record a non-PTY (Codex) session step via cassette files."""
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
            idle_output_timeout=idle_output_timeout,
            max_suppression_seconds=max_suppression_seconds,
            on_pid_resolved=on_pid_resolved,
            enable_deadline_extension=enable_deadline_extension,
            max_extension_seconds=max_extension_seconds,
            marker_dir=marker_dir,
            marker_scope_session_id=marker_scope_session_id,
            stream_parser_factory=stream_parser_factory,
            parent_candidate_normalizer=parent_candidate_normalizer,
            completion_record_types=completion_record_types,
            session_record_types=session_record_types,
            inspector_callback=inspector_callback,
            workload_basenames=workload_basenames,
            on_session_id_resolved=on_session_id_resolved,
            cleanup_budget_seconds=cleanup_budget_seconds,
        )

        if self._scenario_dir is None:
            logger.warning(
                "no scenario_dir set; skipping cassette write for step=%r",
                step_name,
            )
        else:
            cassette_dir = self._scenario_dir / step_name
            cassette_dir.mkdir(parents=True, exist_ok=True)

            lines = (result.stdout or "").splitlines()
            ndjson_content = "".join(fast_dumps(line) + "\n" for line in lines)
            atomic_write(cassette_dir / "codex_stdout.ndjson", ndjson_content)

            meta = {
                "backend": self._backend_name,
                "model": _extract_model(cmd),
                "exit_code": result.returncode,
                "duration_ms": int(result.elapsed_seconds * 1000),
            }
            atomic_write(cassette_dir / "step_meta.json", fast_dumps(meta, indent=True))

        self.recorder.record_non_session_step(
            step_name=step_name,
            tool="run_skill",
            result_summary={
                "exit_code": result.returncode,
                "stdout_head": (result.stdout or "")[:500],
            },
        )

        return result


class ReplayingSubprocessRunner(SubprocessRunner):
    """Replays pre-recorded sessions by step name.

    Consumes the session map from ``ScenarioPlayer.build_session_map()``
    and non-session step results from the scenario manifest. On each call,
    extracts ``SCENARIO_STEP_NAME`` from the command env prefix and
    dispatches to the matching step queue.
    """

    _tmp_replay_dir: tempfile.TemporaryDirectory[str] | None

    def __init__(
        self,
        session_map: dict[str, deque[tuple[Any, Any]]],
        non_session_results: dict[str, dict[str, Any]],
        *,
        player: ScenarioPlayer | None = None,
        skill_snapshots: dict[str, Path] | None = None,
    ) -> None:
        self._sessions = session_map
        self._non_session = non_session_results
        self.player: ScenarioPlayer | None = player
        self.skill_snapshots: dict[str, Path] = skill_snapshots or {}
        self.call_log: list[tuple[str, list[str]]] = []
        self._tmp_replay_dir = None

    def restore_skill_snapshot(
        self, step_name: str, ephemeral_root: Path, session_id: str
    ) -> ValidatedAddDir | None:
        """Restore a skill snapshot for step_name into a fresh ephemeral session dir."""
        snap_path = self.skill_snapshots.get(step_name)
        if snap_path is None:
            return None
        return _restore_skill_snapshot(snap_path, ephemeral_root, session_id)

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: Any | None = None,
        idle_output_timeout: float | None = None,
        max_suppression_seconds: float | None = None,
        on_pid_resolved: Callable[[int, int], None] | None = None,
        enable_deadline_extension: bool = False,
        max_extension_seconds: float = 7200,
        marker_dir: Path | None = None,
        marker_scope_session_id: str | None = None,
        stream_parser_factory: StreamParserFactory | None = None,
        parent_candidate_normalizer: Callable[[dict[str, Any], int], Any] | None = None,
        completion_record_types: frozenset[str] = frozenset({"result"}),
        session_record_types: frozenset[str] = frozenset({"assistant"}),
        inspector_callback: InspectorCallback | None = None,
        workload_basenames: frozenset[str] | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
    ) -> SubprocessResult:
        step_name = (env or {}).get(SCENARIO_STEP_NAME_ENV, "")

        if not step_name:
            raise ValueError(f"SCENARIO_STEP_NAME not found in env kwarg for cmd: {cmd!r}")

        self.call_log.append((step_name, cmd))

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
                cleanup_outcome=_no_owned_process_cleanup(),
            )

        if step_name in self._non_session:
            summary = self._non_session[step_name]
            return SubprocessResult(
                returncode=summary.get("exit_code", 0),
                stdout=summary.get("stdout_head", ""),
                stderr=summary.get("stderr", ""),
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                cleanup_outcome=_no_owned_process_cleanup(),
            )

        raise ScenarioReplayError(
            f"No session or result for step {step_name!r}. "
            f"Available sessions: {sorted(self._sessions.keys())}. "
            f"Available non-session: {sorted(self._non_session.keys())}. "
            f"Ensure the scenario was recorded with step {step_name!r} before replaying."
        )


def build_replay_runner(replay_dir: str) -> ReplayingSubprocessRunner:
    """Build a ReplayingSubprocessRunner from a scenario directory.

    Creates a temporary output directory for the player, then parses the
    scenario manifest and constructs
    the deque-based session map.  All domain logic for replay setup lives here
    (IL-1) rather than in the IL-3 composition root.

    Args:
        replay_dir: Path to a scenario directory produced by a recording run.

    Returns:
        A fully-initialised ReplayingSubprocessRunner ready to replace the
        DefaultSubprocessRunner in a ToolContext.

    Raises:
        RuntimeError: If the scenario is claude-format and ``api_simulator``
            is not installed.
        Exception: Any exception raised by ``player.scenario()`` or
            ``player.build_session_map()`` is re-raised after logging the
            scenario path for context.
    """
    fmt = _detect_backend_format(Path(replay_dir))

    _tmp_replay_dir = tempfile.TemporaryDirectory(prefix="autoskillit-replay-")
    tmp_replay = _tmp_replay_dir.name

    try:
        if fmt == "codex":
            player = CodexScenarioPlayer(
                scenario_dir=replay_dir,
                output_dir=tmp_replay,
                binary_path=str(Path(tmp_replay) / "codex"),
            )
        else:
            try:
                from api_simulator.claude import make_scenario_player
            except ImportError as exc:
                raise RuntimeError(
                    "REPLAY_SCENARIO is set but 'api_simulator' is not installed. "
                    "Install it to enable scenario replay."
                ) from exc
            player = make_scenario_player(
                scenario_dir=replay_dir,
                output_dir=tmp_replay,
                binary_path=str(Path(tmp_replay) / "claude"),
            )
    except Exception:
        _tmp_replay_dir.cleanup()
        raise

    try:
        scenario = player.scenario()
        non_session: dict[str, dict] = {
            record.step_name: record.result_summary or {}
            for record in scenario.step_sequence
            if record.session_dir is None
        }
        raw_map = player.build_session_map()
    except Exception:
        _tmp_replay_dir.cleanup()
        logger.exception("Failed to parse scenario manifest in %r", replay_dir)
        raise

    session_map = {k: deque(v) for k, v in raw_map.items()}
    skill_snapshots = scan_skill_snapshots(Path(replay_dir))
    runner = ReplayingSubprocessRunner(
        session_map, non_session, player=player, skill_snapshots=skill_snapshots
    )
    runner._tmp_replay_dir = _tmp_replay_dir
    return runner
