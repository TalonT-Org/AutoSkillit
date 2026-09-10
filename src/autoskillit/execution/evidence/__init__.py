"""execution/evidence/ — session log, retention, anomalies, tracing, OTLP sink, recording.

Re-exports the full public surface of the nine moved modules.
"""

from autoskillit.execution.evidence._recording_skills import (
    _EPHEMERAL_SESSION_PATTERN,  # noqa: F401
    _FRONTMATTER_PATTERN,  # noqa: F401
    _GATED_PATTERN,  # noqa: F401
    SKILLS_SNAPSHOT_DIR,  # noqa: F401
    _assert_agent_safe_skill_tree,  # noqa: F401
    _extract_ephemeral_add_dir,  # noqa: F401
    build_skills_manifest,  # noqa: F401
    restore_skill_snapshot,  # noqa: F401
    scan_skill_snapshots,  # noqa: F401
    snapshot_skill_dir,  # noqa: F401
    validate_skill_snapshot_members,  # noqa: F401
)
from autoskillit.execution.evidence._session_log_recovery import recover_crashed_sessions
from autoskillit.execution.evidence._session_retention import (
    _CLEAR_MARKER_FILENAME,  # noqa: F401
    _MAX_SESSIONS,  # noqa: F401
    apply_session_retention,  # noqa: F401
    read_telemetry_clear_marker,  # noqa: F401
    write_telemetry_clear_marker,  # noqa: F401
)
from autoskillit.execution.evidence.anomaly_detection import (
    _DATE_SUFFIX_RE,  # noqa: F401
    _MODEL_SHORT_ALIASES,  # noqa: F401
    BENIGN_WCHANS,  # noqa: F401
    OUTCOME_ANOMALY_PID_SENTINEL,  # noqa: F401
    OUTCOME_ANOMALY_SEQ_SENTINEL,  # noqa: F401
    RSS_ABSOLUTE_GROWTH_KB,  # noqa: F401
    RSS_BASELINE_FLOOR_KB,  # noqa: F401
    AnomalyKind,  # noqa: F401
    AnomalySeverity,  # noqa: F401
    _anomaly,  # noqa: F401
    _is_non_anthropic,  # noqa: F401
    _models_match,  # noqa: F401
    _safe_int,  # noqa: F401
    detect_anomalies,  # noqa: F401
    detect_identity_drift,  # noqa: F401
    detect_model_drift,  # noqa: F401
    detect_outcome_anomalies,  # noqa: F401
    normalize_model_id,  # noqa: F401
)
from autoskillit.execution.evidence.linux_tracing import (
    _API_PORT_HEX,  # noqa: F401
    _TCP_STATE_NAMES,  # noqa: F401
    LINUX_TRACING_AVAILABLE,  # noqa: F401
    LinuxTracingHandle,  # noqa: F401
    ProcSnapshot,  # noqa: F401
    TraceEnrollmentRecord,  # noqa: F401
    TraceTarget,  # noqa: F401
    TraceTargetResolutionError,  # noqa: F401
    _parse_net_tcp,  # noqa: F401
    _parse_proc_io,  # noqa: F401
    _parse_proc_status,  # noqa: F401
    _write_enrollment_atomic,  # noqa: F401
    proc_monitor,  # noqa: F401
    read_boot_id,  # noqa: F401
    read_enrollment,  # noqa: F401
    read_proc_snapshot,  # noqa: F401
    read_starttime_ticks,  # noqa: F401
    resolve_trace_target,  # noqa: F401
    start_linux_tracing,  # noqa: F401
    trace_target_from_pid,  # noqa: F401
)
from autoskillit.execution.evidence.otlp_sink import (
    _COUNTER_NAMES,  # noqa: F401
    _FORBIDDEN_KEYS,  # noqa: F401
    _HANDLER_DRAIN_SECONDS,  # noqa: F401
    _MAX_DECODED_REQUEST_BYTES,  # noqa: F401
    _MAX_ENCODED_REQUEST_BYTES,  # noqa: F401
    _MAX_GENERATION_BYTES,  # noqa: F401
    _MODEL_EVIDENCE_OUTCOME_CAPACITY,  # noqa: F401
    _MODEL_EVIDENCE_SESSION_CAPACITY,  # noqa: F401
    _QUEUE_CAPACITY,  # noqa: F401
    _SENTINEL,  # noqa: F401
    _SIGNALS,  # noqa: F401
    _THREAD_JOIN_SECONDS,  # noqa: F401
    _WRITER_POLL_SECONDS,  # noqa: F401
    LocalOtlpSink,  # noqa: F401
    _build_env,  # noqa: F401
    _decode_gzip_bounded,  # noqa: F401
    _has_attribute,  # noqa: F401
    _MalformedBody,  # noqa: F401
    _model_observations,  # noqa: F401
    _ModelObservation,  # noqa: F401
    _OtlpHandler,  # noqa: F401
    _OtlpHTTPServer,  # noqa: F401
    _PayloadTooLarge,  # noqa: F401
    _record_attributes,  # noqa: F401
    _sanitize,  # noqa: F401
    _unique_bool_attribute,  # noqa: F401
    _unique_string_attribute,  # noqa: F401
)
from autoskillit.execution.evidence.recording import (
    RECORD_SCENARIO_DIR_ENV,  # noqa: F401
    RECORD_SCENARIO_ENV,  # noqa: F401
    RECORD_SCENARIO_RECIPE_ENV,  # noqa: F401
    REPLAY_SCENARIO_DIR_ENV,  # noqa: F401
    REPLAY_SCENARIO_ENV,  # noqa: F401
    SCENARIO_STEP_NAME_ENV,  # noqa: F401
    RecordingSubprocessRunner,  # noqa: F401
    ReplayingSubprocessRunner,  # noqa: F401
    ScenarioReplayError,  # noqa: F401
    _detect_backend_format,  # noqa: F401
    _extract_model,  # noqa: F401
    build_replay_runner,  # noqa: F401
)
from autoskillit.execution.evidence.session_index import (
    find_stale_session_archive_references,  # noqa: F401
    read_session_index_rows,  # noqa: F401
    read_tolerant_session_index_rows,  # noqa: F401
)
from autoskillit.execution.evidence.session_log import (
    _append_session_archive_rows,  # noqa: F401
    _primary_model_identifier,  # noqa: F401
    _resolve_session_label,  # noqa: F401
    flush_session_log,  # noqa: F401
    resolve_log_dir,  # noqa: F401
    session_index_lock_path,  # noqa: F401
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
