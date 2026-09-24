# session_log/

Durable, cross-run session-log history under the XDG log root
(`~/.local/share/autoskillit/logs/`). Inclusion criterion: code that writes, indexes,
prunes, or recovers the per-session log directories and the `sessions.jsonl` /
`sessions-archive.jsonl` index. Distinct from `execution/session/`, which processes a single
run's result (exit classification, outcome, retry FSM).

## Architecture Notes

- **`session_log.py`** — `flush_session_log`, `resolve_log_dir`, `session_index_lock_path`,
  `write_execution_candidate_manifest`. XDG base dir spec; log directory names use hyphens
  (never underscores).
- **`_session_log_recovery.py`** — `recover_crashed_sessions` only.
- **`_session_log_retention.py`** — `read_telemetry_clear_marker`, `write_telemetry_clear_marker`,
  `apply_session_retention`.
- **`session_index.py`** — `read_session_index_rows`, `find_stale_session_archive_references`,
  `read_tolerant_session_index_rows`.

Inter-peer coupling (absolute imports): `session_log.py` → `_session_log_retention`,
`session_index`; `_session_log_recovery.py` → `_session_log_retention`, `session_log`.

Cross-package edges:

- `session_log.py` → `evidence.anomaly_detection`, `session._session_model`, `session.turn_usage`
- `_session_log_recovery.py` → `evidence.linux_tracing`; lazily imports `execution.child_outcomes`
  inside `recover_crashed_sessions` because `child_outcomes` imports `resolve_log_dir` from here
  at module level
- `evidence.otlp_sink` → `session_log.resolve_log_dir` (the OTLP stream lives under the log root)

Registered identities keyed on this package's paths: `DURABLE_ARTIFACT_WRITERS`
(`core/types/_type_constants_durable_writers.py`), `WARM_MODULE_NAMES` (`fleet/_startup_warm.py`),
`tests/_retention_surface.py` (line-number keyed — edits above a registered function shift its key),
`tests/arch/test_hook_flock_nonblocking.py`, `tests/infra/test_plugin_source_ratchets.py`,
`tests/infra/test_schema_read_convention.py`.
