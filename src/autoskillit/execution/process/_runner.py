"""Synchronous and protocol-facing managed process runner entry points."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    ChannelConfirmation,
    SubprocessResult,
    TerminationReason,
    get_logger,
)
from autoskillit.execution.process._process_io import create_temp_io, read_temp_output
from autoskillit.execution.process._process_kill import OwnedProcessGroup, spawn_owned_process
from autoskillit.execution.process._process_tether import (
    DEFAULT_TETHER_CEILING_SECONDS,
    TetherSpec,
    wrap_systemd_scope,
)

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.core import InspectorCallback, StreamParser

logger = get_logger(__name__)


def _coalesce_returncode(returncode: int | None) -> int:
    # -1 signals "leader returncode could not be confirmed despite full escalation —
    # see cleanup_evidence for diagnostic detail."
    return returncode if returncode is not None else -1


def run_managed_sync(
    cmd: list[str],
    *,
    cwd: Path | None,
    timeout: float,
    input_data: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_dir: Path | None = None,
    ceiling_seconds: float = DEFAULT_TETHER_CEILING_SECONDS,
    systemd_scope_enabled: bool = False,
) -> SubprocessResult:
    """Sync subprocess execution with temp file I/O and process tree cleanup.

    Same composition pattern as run_managed_async but uses subprocess.Popen
    with start_new_session=True. No channel monitoring — wall-clock timeout only.
    """
    cmd = wrap_systemd_scope(cmd, enabled=systemd_scope_enabled, ceiling_seconds=ceiling_seconds)

    _keep = capture_dir is not None
    with create_temp_io(input_data, capture_dir=capture_dir, keep_streams=_keep) as (
        stdout_file,
        stderr_file,
        stdin_path,
    ):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)
        stdin_handle = open(stdin_path) if stdin_path is not None else None  # noqa: SIM115

        owner: OwnedProcessGroup | None = None
        process: subprocess.Popen[Any] | None = None
        try:
            _env: dict[str, str] | None = dict(env) if env is not None else None
            owner = spawn_owned_process(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=_env,
                start_new_session=True,
                tether=TetherSpec(origin="run_managed", ceiling_seconds=ceiling_seconds),
            )
            process = owner.process

            termination = TerminationReason.NATURAL_EXIT
            deadline = time.monotonic() + timeout
            while owner.observe_exit() is None and time.monotonic() < deadline:
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
            if owner.returncode is None:
                termination = TerminationReason.TIMED_OUT
                logger.warning(
                    "Process %d timed out after %ss, killing tree", process.pid, timeout
                )
            final_returncode, cleanup_result = owner.settle_evidence()
            _coalesced_returncode = _coalesce_returncode(final_returncode)

            stdout_file.close()
            stderr_file.close()
            if capture_dir is not None:
                stdout = ""
                stderr = ""
                _stdout_path = stdout_path
                _stderr_path = stderr_path
            else:
                stdout, stderr = read_temp_output(stdout_path, stderr_path)
                _stdout_path = None
                _stderr_path = None

            return SubprocessResult(
                returncode=_coalesced_returncode,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=process.pid,
                process_group_id=process.pid,
                channel_confirmation=ChannelConfirmation.UNMONITORED,
                stdout_path=_stdout_path,
                stderr_path=_stderr_path,
                cleanup_evidence=cleanup_result,
            )
        except Exception as exc:
            if owner is not None and process is not None and process.returncode is None:
                owner.settle_preserving(exc)
            raise
        finally:
            if stdin_handle is not None:
                try:
                    stdin_handle.close()
                except OSError:
                    pass


class DefaultSubprocessRunner:
    """Implements SubprocessRunner protocol by delegating to run_managed_async."""

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        natural_exit_grace_seconds: float = 3.0,
        linux_tracing_config: LinuxTracingConfig | None = None,
        idle_output_timeout: float | None = None,
        max_suppression_seconds: float | None = None,
        on_pid_resolved: Callable[[int, int], None] | None = None,
        enable_deadline_extension: bool = False,
        max_extension_seconds: float = 7200,
        marker_dir: Path | None = None,
        session_id: str | None = None,
        stream_parser: StreamParser | None = None,
        completion_record_types: frozenset[str] = frozenset({"result"}),
        session_record_types: frozenset[str] = frozenset({"assistant"}),
        inspector_callback: InspectorCallback | None = None,
        workload_basenames: frozenset[str] | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        child_deferral_ceiling: float = 0.0,
        capture_dir: Path | None = None,
        backend_resume_session_id: str = "",
        lifecycle_observation_enabled: bool = False,
        ceiling_seconds: float = DEFAULT_TETHER_CEILING_SECONDS,
        systemd_scope_enabled: bool = False,
    ) -> SubprocessResult:
        from autoskillit.execution.process import run_managed_async

        return await run_managed_async(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            pass_fds=pass_fds,
            stale_threshold=stale_threshold,
            completion_marker=completion_marker,
            session_log_dir=session_log_dir,
            pty_mode=pty_mode,
            input_data=input_data,
            completion_drain_timeout=completion_drain_timeout,
            natural_exit_grace_seconds=natural_exit_grace_seconds,
            linux_tracing_config=linux_tracing_config,
            idle_output_timeout=idle_output_timeout,
            max_suppression_seconds=max_suppression_seconds,
            on_pid_resolved=on_pid_resolved,
            enable_deadline_extension=enable_deadline_extension,
            max_extension_seconds=max_extension_seconds,
            child_deferral_ceiling=child_deferral_ceiling,
            marker_dir=marker_dir,
            session_id=session_id,
            stream_parser=stream_parser,
            completion_record_types=completion_record_types,
            session_record_types=session_record_types,
            inspector_callback=inspector_callback,
            workload_basenames=workload_basenames,
            on_session_id_resolved=on_session_id_resolved,
            capture_dir=capture_dir,
            backend_resume_session_id=backend_resume_session_id,
            lifecycle_observation_enabled=lifecycle_observation_enabled,
            ceiling_seconds=ceiling_seconds,
            systemd_scope_enabled=systemd_scope_enabled,
        )
