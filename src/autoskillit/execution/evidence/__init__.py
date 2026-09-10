"""execution/evidence/ — session log, retention, anomaly detection, linux tracing, OTLP sink, recording.

Re-exports the full public surface of the nine moved modules.
"""

from autoskillit.execution.evidence._recording_skills import (
    SKILLS_SNAPSHOT_DIR,
    _EPHEMERAL_SESSION_PATTERN,
    _FRONTMATTER_PATTERN,
    _GATED_PATTERN,
    _assert_agent_safe_skill_tree,
    _extract_ephemeral_add_dir,
    build_skills_manifest,
    restore_skill_snapshot,
    scan_skill_snapshots,
    snapshot_skill_dir,
    validate_skill_snapshot_members,
)
from autoskillit.execution.evidence._session_log_recovery import recover_crashed_sessions
from autoskillit.execution.evidence._session_retention import (
    _CLEAR_MARKER_FILENAME,
    _MAX_SESSIONS,
    apply_session_retention,
    read_telemetry_clear_marker,
    write_telemetry_clear_marker,
)
from autoskillit.execution.evidence.anomaly_detection import (
    BENIGN_WCHANS,
    OUTCOME_ANOMALY_PID_SENTINEL,
    OUTCOME_ANOMALY_SEQ_SENTINEL,
    RSS_ABSOLUTE_GROWTH_KB,
    RSS_BASELINE_FLOOR_KB,
    AnomalyKind,
    AnomalySeverity,
    _DATE_SUFFIX_RE,
    _MODEL_SHORT_ALIASES,
    _anomaly,
    _is_non_anthropic,
    _models_match,
    _safe_int,
    detect_anomalies,
    detect_identity_drift,
    detect_model_drift,
    detect_outcome_anomalies,
    normalize_model_id,
)
from autoskillit.execution.evidence.linux_tracing import (
    LINUX_TRACING_AVAILABLE,
    LinuxTracingHandle,
    ProcSnapshot,
    TraceEnrollmentRecord,
    TraceTarget,
    TraceTargetResolutionError,
    _API_PORT_HEX,
    _TCP_STATE_NAMES,
    _parse_net_tcp,
    _parse_proc_io,
    _parse_proc_status,
    _write_enrollment_atomic,
    proc_monitor,
    read_boot_id,
    read_enrollment,
    read_proc_snapshot,
    read_starttime_ticks,
    resolve_trace_target,
    start_linux_tracing,
    trace_target_from_pid,
)
from autoskillit.execution.evidence.otlp_sink import (
    LocalOtlpSink,
    _COUNTER_NAMES,
    _FORBIDDEN_KEYS,
    _HANDLER_DRAIN_SECONDS,
    _MAX_DECODED_REQUEST_BYTES,
    _MAX_ENCODED_REQUEST_BYTES,
    _MAX_GENERATION_BYTES,
    _MODEL_EVIDENCE_OUTCOME_CAPACITY,
    _MODEL_EVIDENCE_SESSION_CAPACITY,
    _MalformedBody,
    _ModelObservation,
    _OtlpHTTPServer,
    _OtlpHandler,
    _PayloadTooLarge,
    _QUEUE_CAPACITY,
    _SENTINEL,
    _SIGNALS,
    _THREAD_JOIN_SECONDS,
    _WRITER_POLL_SECONDS,
    _build_env,
    _decode_gzip_bounded,
    _has_attribute,
    _model_observations,
    _record_attributes,
    _sanitize,
    _unique_bool_attribute,
    _unique_string_attribute,
)
from autoskillit.execution.evidence.recording import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    REPLAY_SCENARIO_DIR_ENV,
    REPLAY_SCENARIO_ENV,
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    SCENARIO_STEP_NAME_ENV,
    ScenarioReplayError,
    _detect_backend_format,
    _extract_model,
    build_replay_runner,
)
from autoskillit.execution.evidence.session_index import (
    find_stale_session_archive_references,
    read_session_index_rows,
    read_tolerant_session_index_rows,
)
from autoskillit.execution.evidence.session_log import (
    _append_session_archive_rows,
    _primary_model_identifier,
    _resolve_session_label,
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
