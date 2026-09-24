# evidence/

Runtime observability signals collected for post-hoc diagnosis. Inclusion criterion:
always-on signal collection about a running session — process-level anomalies, Linux
`/proc` tracing, and the local OTLP receiver. Durable session-log history lives in
`execution/session_log/`; record/replay tooling lives in `execution/recording/`.

## Architecture Notes

- **`anomaly_detection.py`** — `detect_anomalies`, `AnomalyKind`, `AnomalySeverity`, plus
  internal drift and outcome helpers. Depends only on `core`.
- **`linux_tracing.py`** — `LINUX_TRACING_AVAILABLE`, `LinuxTracingHandle`, `ProcSnapshot`,
  `start_linux_tracing`, `read_enrollment`, `resolve_trace_target`. Depends only on `core`
  (`config.LinuxTracingConfig` under `TYPE_CHECKING`).
- **`otlp_sink.py`** — `LocalOtlpSink` plus internal OTLP/HTTP primitives (not in
  `execution.__all__`, not re-exported by this gateway). Imports
  `session_log.session_log.resolve_log_dir` to place `otlp.jsonl` under the log root.
- **`otlp_tokens.py`** — Bounded request-correlated token projection from native OTLP logs.
  Shared helpers used by report indexing are `iter_scoped_log_records`,
  `claude_request_usage`, `unique_count_attribute`, `unique_float_attribute`,
  `unique_flag_attribute`, `has_attribute`, and the `CLAUDE_CODE_SCOPE_NAME` /
  `CODEX_SCOPE_NAME` literal vocabulary. Public surface; consumed cross-package.
- **`report_walk.py`** — Resumable walk over OTLP, session archive, and session projection
  sources. `SourceGapError.source` names the stale cursor's source; the consumer is
  `execution/report_index.py`.
- **`reader/`** — private contracts and protocol validation for evidence readers, consumed
  by `execution/evidence_reader.py` (#4664). Outside this package's signal-collection
  criterion and outside #4967's scope.

`__init__.py` re-exports `anomaly_detection` and `linux_tracing` only. It must not import
`otlp_sink`: `otlp_sink` imports `session_log`, and `session_log.session_log` imports
`evidence.anomaly_detection`, so re-exporting `LocalOtlpSink` here would make importing
either package initialize the other.
