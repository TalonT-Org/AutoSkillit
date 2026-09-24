"""Tests verifying process.py decomposition into focused sub-modules.

P8-2: Each _process_*.py sub-module exports its expected symbols.
process.py remains a re-export facade for all public symbols.
"""

from __future__ import annotations

import functools
import inspect
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import anyio
import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_EXPECTED_PROCESS_SYMBOLS: frozenset[str] = frozenset(
    {
        "DEFAULT_TETHER_CEILING_SECONDS",
        "TETHER_LEASE_RENEW_SECONDS",
        "TETHER_LEASE_SECONDS",
        "TETHER_SWEEP_INTERVAL_SECONDS",
        "CaptureReadError",
        "CaptureSetupError",
        "CodexOrphanReapResult",
        "DaemonOrphanReapResult",
        "DefaultSubprocessRunner",
        "OrphanedAutoSkillitDaemon",
        "OrphanedCodexProcess",
        "OrphanedTetherRecord",
        "OwnedProcessGroup",
        "TetherRecord",
        "TetherSpec",
        "TetherSweepOutcome",
        "TetherSweepReport",
        "_extract_stdout_session_id",
        "_active_liveness_signals",
        "_resolve_session_id",
        "RaceAccumulator",
        "RaceSignals",
        "_has_active_api_connection",
        "_has_active_child_processes",
        "_has_active_execution_marker",
        "_heartbeat",
        "_jsonl_contains_marker",
        "_jsonl_has_record_type",
        "_jsonl_last_record_type",
        "_marker_is_standalone",
        "_session_log_monitor",
        "_watch_heartbeat",
        "_watch_process",
        "_watch_session_log",
        "async_kill_process_tree",
        "create_temp_io",
        "decide_termination_action",
        "default_tether_dir",
        "execute_termination_action",
        "fold_lifecycle_evidence",
        "fold_lifecycle_evidence_path",
        "find_orphaned_codex_processes",
        "find_orphaned_autoskillit_daemons",
        "find_orphaned_tethers",
        "format_orphaned_tether_fields",
        "kill_process_tree",
        "probe_systemd_scope_available",
        "pty_wrap_command",
        "read_temp_output",
        "reap_orphaned_codex_processes",
        "reap_orphaned_autoskillit_daemons",
        "renew_tether",
        "resolve_termination",
        "run_managed_async",
        "run_managed_sync",
        "summarize_capture",
        "spawn_owned_process",
        "sweep_orphaned_tethers",
        "sweep_orphaned_tethers_async",
        "update_tether_workload",
        "wrap_systemd_scope",
    }
)


def test_process_kill_exports():
    """kill_process_tree and async_kill_process_tree are defined in _process_kill submodule."""
    from autoskillit.execution.process._process_kill import (
        async_kill_process_tree,
        kill_process_tree,
    )

    assert callable(kill_process_tree)
    assert kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"
    assert callable(async_kill_process_tree)
    assert async_kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"


def test_owned_process_group_exports() -> None:
    from autoskillit.execution.process._lifecycle.owned_group import (
        OwnedProcessCleanupError,
        OwnedProcessGroup,
        ProcessObservationSnapshot,
        spawn_owned_process,
    )

    for symbol in (
        ProcessObservationSnapshot,
        OwnedProcessCleanupError,
        OwnedProcessGroup,
        spawn_owned_process,
    ):
        assert symbol.__module__ == "autoskillit.execution.process._lifecycle.owned_group"


def test_process_pty_exports():
    """pty_wrap_command is defined in _process_pty submodule."""
    from autoskillit.execution.process._process_pty import pty_wrap_command

    assert callable(pty_wrap_command)
    assert pty_wrap_command.__module__ == "autoskillit.execution.process._process_pty"


def test_process_jsonl_exports():
    """_jsonl_contains_marker, _jsonl_has_record_type, and _marker_is_standalone
    are defined in _process_jsonl submodule."""
    from autoskillit.execution.process._process_jsonl import (
        _jsonl_contains_marker,
        _jsonl_has_record_type,
        _marker_is_standalone,
    )

    assert callable(_jsonl_contains_marker)
    assert _jsonl_contains_marker.__module__ == "autoskillit.execution.process._process_jsonl"
    assert callable(_jsonl_has_record_type)
    assert _jsonl_has_record_type.__module__ == "autoskillit.execution.process._process_jsonl"
    assert callable(_marker_is_standalone)
    assert _marker_is_standalone.__module__ == "autoskillit.execution.process._process_jsonl"


def test_process_io_exports():
    """create_temp_io and read_temp_output are defined in _process_io submodule."""
    from autoskillit.execution.process._process_io import create_temp_io, read_temp_output

    assert callable(create_temp_io)
    assert create_temp_io.__module__ == "autoskillit.execution.process._process_io"
    assert callable(read_temp_output)
    assert read_temp_output.__module__ == "autoskillit.execution.process._process_io"


def test_process_monitor_exports():
    """_heartbeat, _session_log_monitor, and _has_active_api_connection
    are defined in _process_monitor submodule."""
    from autoskillit.execution.process._process_monitor import (
        _has_active_api_connection,
        _has_active_execution_marker,
        _heartbeat,
        _session_log_monitor,
    )

    assert callable(_heartbeat)
    assert _heartbeat.__module__ == "autoskillit.execution.process._process_monitor"
    assert callable(_session_log_monitor)
    assert _session_log_monitor.__module__ == "autoskillit.execution.process._process_monitor"
    assert callable(_has_active_api_connection)
    assert (
        _has_active_api_connection.__module__ == "autoskillit.execution.process._process_monitor"
    )
    assert callable(_has_active_execution_marker)
    assert (
        _has_active_execution_marker.__module__ == "autoskillit.execution.process._process_monitor"
    )


def test_process_race_exports():
    """Race types/functions are defined in _process_race submodule."""
    from autoskillit.execution.process._process_race import (
        RaceAccumulator,
        RaceSignals,
        _extract_stdout_session_id,
        _watch_heartbeat,
        _watch_process,
        _watch_session_log,
        fold_lifecycle_evidence,
        fold_lifecycle_evidence_path,
        resolve_termination,
    )

    assert RaceAccumulator.__module__ == "autoskillit.execution.process._process_race"
    assert RaceSignals.__module__ == "autoskillit.execution.process._process_race"
    assert callable(resolve_termination)
    assert resolve_termination.__module__ == "autoskillit.execution.process._process_race"
    assert callable(_watch_heartbeat)
    assert _watch_heartbeat.__module__ == "autoskillit.execution.process._process_race"
    assert _watch_process.__module__ == "autoskillit.execution.process._process_race"
    assert _extract_stdout_session_id.__module__ == "autoskillit.execution.process._process_race"
    assert _watch_session_log.__module__ == "autoskillit.execution.process._process_race"
    assert callable(fold_lifecycle_evidence)
    assert fold_lifecycle_evidence.__module__ == "autoskillit.execution.process._process_race"
    assert callable(fold_lifecycle_evidence_path)
    assert fold_lifecycle_evidence_path.__module__ == "autoskillit.execution.process._process_race"


def test_supervisory_watchers_live_in_coordinator_module() -> None:
    from autoskillit.execution.process._race_watchers import (
        _watch_child_activity,
        _watch_completion_eligibility,
        _watch_stdout_idle,
    )

    for watcher in (
        _watch_stdout_idle,
        _watch_child_activity,
        _watch_completion_eligibility,
    ):
        assert watcher.__module__ == "autoskillit.execution.process._race_watchers"


def test_race_coordinator_lives_in_watcher_module() -> None:
    from autoskillit.execution import process
    from autoskillit.execution.process._race_watchers import (
        _await_race_and_drain,
        _enroll_race_watchers,
    )

    for helper in (_enroll_race_watchers, _await_race_and_drain):
        assert callable(helper)
        assert helper.__module__ == "autoskillit.execution.process._race_watchers"

    facade_source = Path(process.__file__).read_text()
    assert "def _enroll_race_watchers(" not in facade_source
    assert "def _await_race_and_drain(" not in facade_source
    for export in (
        "_extract_stdout_session_id",
        "_watch_heartbeat",
        "_watch_process",
        "_watch_session_log",
    ):
        assert hasattr(process, export)


@pytest.mark.parametrize(
    (
        "session_log_dir",
        "lifecycle_observation_enabled",
        "idle_output_timeout",
        "expected_watchers",
    ),
    [
        (None, False, None, ("process", "heartbeat")),
        (
            Path("session-log"),
            False,
            None,
            ("process", "heartbeat", "stdout_session_id", "session_log"),
        ),
        (None, True, None, ("process", "heartbeat", "completion")),
        (None, False, 30.0, ("process", "heartbeat", "stdout_idle")),
    ],
)
def test_race_enrollment_uses_supplied_watchers(
    session_log_dir: Path | None,
    lifecycle_observation_enabled: bool,
    idle_output_timeout: float | None,
    expected_watchers: tuple[str, ...],
) -> None:
    from autoskillit.execution.process._process_race import RaceAccumulator
    from autoskillit.execution.process._race_watchers import _enroll_race_watchers

    class CapturingTaskGroup:
        def __init__(self) -> None:
            self.scheduled: list[tuple[object, tuple[object, ...]]] = []

        def start_soon(self, func, *args) -> None:
            self.scheduled.append((func, args))

    watchers = {
        "process": Mock(),
        "heartbeat": Mock(),
        "stdout_session_id": Mock(),
        "session_log": Mock(),
        "completion": Mock(),
        "stdout_idle": Mock(),
    }
    tg = CapturingTaskGroup()
    acc = RaceAccumulator()
    trigger = anyio.Event()
    channel_b_ready = anyio.Event()
    stdout_session_id_ready = anyio.Event()
    channel_b_selected = anyio.Event()
    line_driver = Mock()
    owner = Mock()
    process = Mock()
    capture_file = BytesIO()
    stream_parser = Mock()
    lifecycle_parser = Mock()
    timeout_scope_ref: list[anyio.CancelScope | None] = [None]
    stdout_path = Path("stdout")
    _enroll_race_watchers(
        tg,
        _watch_process=watchers["process"],
        _watch_heartbeat=watchers["heartbeat"],
        _extract_stdout_session_id=watchers["stdout_session_id"],
        _watch_session_log=watchers["session_log"],
        _watch_completion_eligibility=watchers["completion"],
        _watch_stdout_idle=watchers["stdout_idle"],
        owner=owner,
        line_driver_session=line_driver,
        process=process,
        capture_file=capture_file,
        acc=acc,
        trigger=trigger,
        stdout_path=stdout_path,
        completion_record_types=frozenset({"result"}),
        completion_marker="done",
        stream_parser=stream_parser,
        heartbeat_poll=0.1,
        session_log_dir=session_log_dir,
        stale_threshold=1.0,
        spawn_time=0.0,
        session_record_types=frozenset(),
        observed_pid=1,
        channel_b_ready=channel_b_ready,
        phase1_poll=0.1,
        phase2_poll=0.1,
        phase1_timeout=1.0,
        session_id_timeout=1.0,
        stdout_session_id_ready=stdout_session_id_ready,
        max_suppression_seconds=None,
        marker_dir=None,
        session_id=None,
        on_session_id_resolved=None,
        backend_resume_session_id="",
        channel_b_selected=channel_b_selected,
        lifecycle_observation_enabled=lifecycle_observation_enabled,
        lifecycle_parser=lifecycle_parser,
        completion_drain_timeout=1.0,
        child_deferral_ceiling=1.0,
        idle_output_timeout=idle_output_timeout,
        inspector_callback=None,
        timeout_scope_ref=timeout_scope_ref,
    )

    scheduled_funcs = [
        func.func if isinstance(func, functools.partial) else func for func, _args in tg.scheduled
    ]
    assert scheduled_funcs == [watchers[name] for name in expected_watchers]
    assert tg.scheduled[0] == (watchers["process"], (owner, acc, trigger))
    heartbeat, heartbeat_args = tg.scheduled[1]
    assert isinstance(heartbeat, functools.partial)
    assert heartbeat.args == ()
    assert heartbeat.keywords == {"stream_parser": stream_parser, "_poll_interval": 0.1}
    assert heartbeat_args == (stdout_path, frozenset({"result"}), "done", acc, trigger)

    if session_log_dir is not None:
        stdout_session_id, stdout_session_id_args = tg.scheduled[2]
        assert isinstance(stdout_session_id, functools.partial)
        assert stdout_session_id.args == ()
        assert stdout_session_id.keywords == {
            "stream_parser": stream_parser,
            "on_session_id_resolved": None,
        }
        assert stdout_session_id_args == (stdout_path, acc, stdout_session_id_ready)
        assert tg.scheduled[3][1][0] == session_log_dir

    if lifecycle_observation_enabled:
        assert tg.scheduled[2][1] == (
            acc,
            trigger,
            channel_b_selected,
            1.0,
            1.0,
            lifecycle_parser,
            False,
        )

    if idle_output_timeout is not None:
        stdout_idle, stdout_idle_args = tg.scheduled[2]
        assert isinstance(stdout_idle, functools.partial)
        assert stdout_idle_args == ()
        assert stdout_idle.args[:4] == (stdout_path, idle_output_timeout, acc, trigger)
        assert stdout_idle.keywords["timeout_scope_ref"] is timeout_scope_ref

    line_driver.start.assert_called_once_with(
        tg,
        process,
        capture_file=capture_file,
        trigger=trigger,
    )


def test_process_facade_reexports_all_public_symbols():
    """process.py facade re-exports exactly the expected public symbols."""
    from autoskillit.execution import process

    assert hasattr(process, "__all__")
    assert set(process.__all__) == _EXPECTED_PROCESS_SYMBOLS, (
        f"process.__all__ mismatch.\n"
        f"  Extra   : {set(process.__all__) - _EXPECTED_PROCESS_SYMBOLS}\n"
        f"  Missing : {_EXPECTED_PROCESS_SYMBOLS - set(process.__all__)}"
    )


def test_default_subprocess_runner_pty_mode_default_false():

    from autoskillit.execution.process import DefaultSubprocessRunner

    sig = inspect.signature(DefaultSubprocessRunner.__call__)
    assert sig.parameters["pty_mode"].default is False


def test_run_managed_async_pty_mode_default_false():

    from autoskillit.execution.process import run_managed_async

    sig = inspect.signature(run_managed_async)
    assert sig.parameters["pty_mode"].default is False
