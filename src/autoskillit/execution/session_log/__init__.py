"""execution/session_log/ — durable cross-run session-log history.

Write (flush), index, retain, and recover the per-session log directories under the XDG
log root. Distinct from execution/session/, which processes a single run's result.
"""

from autoskillit.execution.session_log._session_log_recovery import recover_crashed_sessions
from autoskillit.execution.session_log._session_retention import (
    read_telemetry_clear_marker,
    write_telemetry_clear_marker,
)
from autoskillit.execution.session_log.session_index import read_session_index_rows
from autoskillit.execution.session_log.session_log import (
    flush_session_log,
    resolve_log_dir,
    session_index_lock_path,
    write_execution_candidate_manifest,
)

__all__ = [
    "flush_session_log",
    "resolve_log_dir",
    "session_index_lock_path",
    "write_execution_candidate_manifest",
    "recover_crashed_sessions",
    "read_telemetry_clear_marker",
    "write_telemetry_clear_marker",
    "read_session_index_rows",
]
