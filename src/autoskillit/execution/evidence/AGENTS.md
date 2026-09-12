# evidence/

Session log, retention, anomaly detection, linux tracing, OTLP sink, recording —
moved out of `execution/` top level so `execution/` holds ≤1 Python file
(plus `evidence_reader.py`, owned by #4664).

## Architecture Notes

The moved modules each retain their original contract:

- **`session_log.py`** — `flush_session_log`, `resolve_log_dir`,
  `session_index_lock_path`. Uses XDG base dir spec; log directory names use
  hyphens (never underscores).
- **`_session_log_recovery.py`** — `recover_crashed_sessions` only.
- **`_session_retention.py`** — `read_telemetry_clear_marker`,
  `write_telemetry_clear_marker`, `apply_session_retention`.
- **`session_index.py`** — `read_session_index_rows`,
  `find_stale_session_archive_references`, `read_tolerant_session_index_rows`.
- **`anomaly_detection.py`** — `detect_anomalies`, `AnomalyKind`,
  `AnomalySeverity`, plus internal drift and outcome helpers.
- **`linux_tracing.py`** — `LINUX_TRACING_AVAILABLE`, `LinuxTracingHandle`,
  `ProcSnapshot`, `start_linux_tracing`, `read_enrollment`,
  `resolve_trace_target`.
- **`otlp_sink.py`** — `LocalOtlpSink` plus internal OTLP/HTTP primitives
  (not in `execution.__all__`).
- **`recording.py`** — `RecordingSubprocessRunner`, `ReplayingSubprocessRunner`,
  `ScenarioReplayError`, `build_replay_runner`, plus scenario env vars.
- **`_recording_skills.py`** — `restore_skill_snapshot`,
  `scan_skill_snapshots`, `snapshot_skill_dir`, plus internal skill-tree
  helpers.

Inter-peer coupling (preserved as relative imports after the move):

- `session_log.py` → `_session_retention`, `anomaly_detection`, `session_index`
- `_session_log_recovery.py` → `linux_tracing`, `session_log`

Activation guards:

- `recording.py` and `_recording_skills.py` only activate when
  `AUTOSKILLIT_RECORD_SESSION` is set; production paths never touch them.
