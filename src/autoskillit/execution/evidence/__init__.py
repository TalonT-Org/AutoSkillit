"""execution/evidence/ — session log, retention, anomalies, tracing, OTLP sink, recording.

Re-exports the full public surface of the nine moved modules.
"""

from autoskillit.execution.evidence._recording_skills import (
    restore_skill_snapshot,
    scan_skill_snapshots,
    snapshot_skill_dir,
)
from autoskillit.execution.evidence._session_log_recovery import recover_crashed_sessions
from autoskillit.execution.evidence._session_retention import (
    read_telemetry_clear_marker,
    write_telemetry_clear_marker,
)
from autoskillit.execution.evidence.anomaly_detection import (
    AnomalyKind,
    AnomalySeverity,
    detect_anomalies,
)
from autoskillit.execution.evidence.linux_tracing import (
    LINUX_TRACING_AVAILABLE,
    LinuxTracingHandle,
    ProcSnapshot,
    read_boot_id,
    read_starttime_ticks,
    start_linux_tracing,
)
from autoskillit.execution.evidence.recording import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    REPLAY_SCENARIO_DIR_ENV,
    REPLAY_SCENARIO_ENV,
    SCENARIO_STEP_NAME_ENV,
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    ScenarioReplayError,
    build_replay_runner,
)
from autoskillit.execution.evidence.session_index import read_session_index_rows
from autoskillit.execution.evidence.session_log import (
    flush_session_log,
    resolve_log_dir,
    session_index_lock_path,
)

__all__ = [
    # session_log
    "flush_session_log",
    "resolve_log_dir",
    "session_index_lock_path",
    # _session_log_recovery
    "recover_crashed_sessions",
    # _session_retention
    "read_telemetry_clear_marker",
    "write_telemetry_clear_marker",
    # anomaly_detection
    "detect_anomalies",
    "AnomalyKind",
    "AnomalySeverity",
    # session_index
    "read_session_index_rows",
    # linux_tracing
    "LINUX_TRACING_AVAILABLE",
    "LinuxTracingHandle",
    "ProcSnapshot",
    "read_boot_id",
    "read_starttime_ticks",
    "start_linux_tracing",
    # recording
    "RecordingSubprocessRunner",
    "ReplayingSubprocessRunner",
    "ScenarioReplayError",
    "build_replay_runner",
    "RECORD_SCENARIO_ENV",
    "RECORD_SCENARIO_DIR_ENV",
    "RECORD_SCENARIO_RECIPE_ENV",
    "REPLAY_SCENARIO_ENV",
    "REPLAY_SCENARIO_DIR_ENV",
    "SCENARIO_STEP_NAME_ENV",
    # _recording_skills
    "restore_skill_snapshot",
    "scan_skill_snapshots",
    "snapshot_skill_dir",
]
