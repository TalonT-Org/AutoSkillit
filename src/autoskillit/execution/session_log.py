"""File-based session diagnostics log writer.

Writes structured JSON logs to a global, XDG-aware directory. Each headless
session gets its own directory keyed by session ID, containing process trace
data, a session summary, and flagged anomalies. A bounded derived index provides
quick scanning across retained committed sessions.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from autoskillit.core import (
        CampaignProtector,
        NativeShellCaptureDiagnostic,
        ProviderOutcome,
        RecipeIdentity,
        SessionLocator,
        SessionTelemetry,
    )


from autoskillit.core import (
    ArtifactLease,
    ModelIdentity,
    atomic_write,
    default_log_dir,
    get_logger,
    iter_merged_assistant_turns,
    write_versioned_json,
)
from autoskillit.core import fast_dumps as _fast_dumps
from autoskillit.execution.anomaly_detection import (
    detect_anomalies,
    detect_identity_drift,
    detect_model_drift,
    detect_outcome_anomalies,
)

logger = get_logger(__name__)

_MAX_SESSIONS = 2000


def _primary_model_identifier(token_usage: dict[str, Any] | None) -> str:
    """Return the model name with the most output tokens from model_breakdown.

    Returns "" when token_usage is absent or model_breakdown is empty.
    """
    if not token_usage:
        return ""
    mb = token_usage.get("model_breakdown", {})
    if not isinstance(mb, dict) or not mb:
        return ""
    return max(mb, key=lambda m: mb[m].get("output_tokens", 0) if isinstance(mb[m], dict) else 0)


_CLEAR_MARKER_FILENAME = ".telemetry_cleared_at"


def resolve_log_dir(log_dir: str) -> Path:
    """Resolve session log directory. Empty string = platform default."""
    if log_dir:
        return Path(log_dir).expanduser()
    return default_log_dir()


def session_index_lock_path(log_root: Path) -> Path:
    """Return the stable lease path for session-index transactions."""
    return Path(log_root) / ".locks" / "sessions-index.lock"


def _read_index_rows(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_telemetry_clear_marker(log_root: Path) -> None:
    """Write the current UTC timestamp as a telemetry-clear fence.

    Called when any pipeline log is cleared via clear=True. On the next server
    startup, _state._initialize reads this marker and excludes sessions that
    predate it from load_from_log_dir replay, preventing double-counting.

    Silently no-ops on any error — never raises.
    """
    try:
        log_root = Path(log_root)
        log_root.mkdir(parents=True, exist_ok=True)
        atomic_write(log_root / _CLEAR_MARKER_FILENAME, datetime.now(UTC).isoformat())
    except Exception:
        logger.debug("write_telemetry_clear_marker failed", exc_info=True)


def read_telemetry_clear_marker(log_root: Path) -> datetime | None:
    """Read the persisted telemetry-clear timestamp, or None if absent/corrupt."""
    try:
        text = (Path(log_root) / _CLEAR_MARKER_FILENAME).read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(text)
    except (OSError, ValueError):
        return None


def _resolve_session_label(step_name: str, dispatch_id: str) -> str:
    """Derive a non-empty session label for telemetry file identification.

    Recipe steps use step_name. Fleet dispatches use dispatch_id.
    Ad-hoc sessions get a fallback label.
    """
    if step_name:
        return step_name
    if dispatch_id:
        return f"dispatch:{dispatch_id}"
    return "(ad-hoc)"


def flush_session_log(
    *,
    log_dir: str,
    cwd: str,
    kitchen_id: str = "",
    caller_session_id: str = "",
    order_id: str = "",
    campaign_id: str = "",
    dispatch_id: str = "",
    project_dir: str = "",
    build_protected_campaign_ids: CampaignProtector | None = None,
    session_id: str,
    pid: int,
    skill_command: str,
    success: bool,
    subtype: str,
    exit_code: int,
    start_ts: str,
    proc_snapshots: list[dict[str, object]] | None,
    end_ts: str = "",
    elapsed_seconds: float | None = None,
    termination_reason: str = "",
    kill_reason: str = "",
    provider_outcome: ProviderOutcome,
    recipe_identity: RecipeIdentity,
    snapshot_interval_seconds: float = 0.0,
    step_name: str = "",
    cli_subtype: str = "",
    write_path_warnings: list[str] | None = None,
    write_call_count: int = 0,
    fs_writes_detected: bool = False,
    git_writes_detected: bool = False,
    file_changes_count: int = 0,
    clone_contamination_reverted: bool = False,
    tracked_comm: str | None = None,
    comm_aliases: frozenset[str] = frozenset(),
    exception_text: str = "",
    orphaned_tool_result: bool = False,
    raw_stdout: str = "",
    last_stop_reason: str = "",
    has_thinking_only_turn: bool = False,
    api_retry_count: int = 0,
    api_retry_last_error: str = "",
    api_retry_last_status: int | None = None,
    api_retry_exhausted: bool = False,
    ndjson_unknown_event_count: int = 0,
    ndjson_unknown_item_count: int = 0,
    versions: dict[str, Any] | None = None,
    model_identity: ModelIdentity = ModelIdentity.unknown(),
    max_sessions: int | None = None,
    is_resume: bool = False,
    session_locator: SessionLocator | None = None,
    backend: Literal["claude-code", "codex"] = "claude-code",
    channel_b_capable: bool = True,
    backend_authority: Mapping[str, object] | None = None,
    launch_contract_digest: str = "",
    telemetry: SessionTelemetry,
    outcome_fields: dict[str, int | str] | None = None,
    outcome_invariant_violated: bool = False,
    outcome_qualifier: str | None = None,
    native_shell_capture: NativeShellCaptureDiagnostic | None = None,
    is_crash_recovery: bool = False,
) -> None:
    """Flush session diagnostics to disk.

    Writes proc_trace.jsonl, summary.json, anomalies.jsonl (if any),
    and transactionally projects the session into sessions.jsonl. Applies retention
    to keep at most ``_MAX_SESSIONS`` session directories (default 2000,
    configurable via ``linux_tracing.max_sessions``).

    When step_name is provided, also writes token_usage.json, step_timing.json,
    and (if telemetry.audit_record is set) audit_log.json to the session directory
    for recovery at next server startup.
    """
    token_usage = telemetry.token_usage
    timing_seconds = telemetry.timing_seconds
    audit_record = telemetry.audit_record
    loc_insertions = telemetry.loc_insertions
    loc_deletions = telemetry.loc_deletions
    effective_write_path_warnings: list[str] = (
        write_path_warnings if write_path_warnings is not None else []
    )
    log_root = resolve_log_dir(log_dir)
    if session_id and is_resume:
        dir_name = f"{session_id}_{start_ts.replace(':', '-')}"
    elif session_id:
        dir_name = session_id
    else:
        dir_name = f"no_session_{start_ts.replace(':', '-')}"

    from autoskillit.execution.backends import CompositeSessionLocator

    _codex_log_str: str | None = None

    if session_locator is not None:
        _locator = session_locator
    else:
        _composite = CompositeSessionLocator()
        _locator = _composite.locator_for(backend)

    if channel_b_capable:
        cc_log = _locator.session_log_path(cwd, session_id)
        cc_log_str = str(cc_log) if cc_log else None
    else:
        cc_log = None
        cc_log_str = None
        if session_id and not session_id.startswith(("no_session_", "crashed_")):
            try:
                _codex_found = _locator.locate_session(session_id)
                _codex_log_str = str(_codex_found) if _codex_found else None
            except Exception:
                logger.debug("session_locate_failed", backend=backend, exc_info=True)

    if cc_log and not cc_log.exists():
        logger.warning("claude_code_log_not_found", path=cc_log_str, session_id=session_id)

    silent_gap_seconds: float | None = None
    if cc_log and cc_log.exists() and end_ts:
        try:
            cc_log_mtime = cc_log.stat().st_mtime
            end_dt = datetime.fromisoformat(end_ts)
            silent_gap_seconds = max(0.0, end_dt.timestamp() - cc_log_mtime)
        except (OSError, ValueError):
            pass

    _cb_request_ids: list[str] = []
    _cb_turn_timestamps: list[str] = []
    _cb_turn_tool_calls: list[tuple[str, ...]] = []
    if cc_log and cc_log.exists():
        try:
            _text = cc_log.read_text(encoding="utf-8", errors="replace")
            for _turn in iter_merged_assistant_turns(_text):
                _cb_request_ids.append(_turn.request_id)
                _cb_turn_timestamps.append(_turn.timestamp)
                _cb_turn_tool_calls.append(_turn.tool_names)
        except OSError:
            logger.debug("channel_b_log_read_error", path=cc_log_str, exc_info=True)

    with ArtifactLease.acquire_exclusive(session_index_lock_path(log_root), blocking=True):
        sessions_dir = log_root / "sessions"
        session_dir = sessions_dir / dir_name
        sessions_dir.mkdir(parents=True, exist_ok=True)
        for abandoned in sessions_dir.iterdir():
            if abandoned.is_dir() and not (abandoned / "summary.json").is_file():
                shutil.rmtree(abandoned, ignore_errors=True)
        summary_path = session_dir / "summary.json"
        reuse_committed_recovery = is_crash_recovery and summary_path.is_file()
        if not reuse_committed_recovery:
            session_dir.mkdir(parents=True, exist_ok=True)
        publish_artifacts = not reuse_committed_recovery

        snapshot_count = 0
        peak_rss_kb = 0
        peak_oom_score = 0
        peak_fd_ratio = 0.0
        anomalies: list[dict[str, object]] = []
        _effective_tracked_comm: str | None = tracked_comm
        _tracked_comm_drift: bool = False

        # Write proc_trace.jsonl
        if proc_snapshots:
            snapshot_count = len(proc_snapshots)
            if publish_artifacts:
                trace_path = session_dir / "proc_trace.jsonl"
                with trace_path.open("w") as f:
                    for seq, snap in enumerate(proc_snapshots):
                        record = {
                            "ts": snap.get("captured_at") or start_ts,
                            "seq": seq,
                            "pid": pid,
                            **snap,
                            "event": snap.get("event", "snapshot"),
                        }
                        f.write(_fast_dumps(record, sort_keys=True) + "\n")

            for snap in proc_snapshots:
                rss = snap.get("vm_rss_kb", 0)
                if isinstance(rss, int) and rss > peak_rss_kb:
                    peak_rss_kb = rss
                oom = snap.get("oom_score", 0)
                if isinstance(oom, int) and oom > peak_oom_score:
                    peak_oom_score = oom
                fd_count = snap.get("fd_count", 0)
                fd_limit = snap.get("fd_soft_limit", 0)
                if isinstance(fd_count, int) and isinstance(fd_limit, int) and fd_limit > 0:
                    peak_fd_ratio = max(peak_fd_ratio, fd_count / fd_limit)

            if _effective_tracked_comm is None:
                comm_counts: dict[str, int] = {}
                for snap in proc_snapshots:
                    comm = snap.get("comm", "")
                    if comm and isinstance(comm, str):
                        comm_counts[comm] = comm_counts.get(comm, 0) + 1
                if comm_counts:
                    _effective_tracked_comm = max(comm_counts, key=lambda key: comm_counts[key])

            if _effective_tracked_comm:
                comms_seen = {
                    snap.get("comm", "") for snap in proc_snapshots if snap.get("comm", "")
                }
                if len(comms_seen) > 1:
                    _tracked_comm_drift = True

            anomalies = detect_anomalies(proc_snapshots, pid)
            if _effective_tracked_comm:
                anomalies.extend(
                    detect_identity_drift(
                        proc_snapshots, _effective_tracked_comm, comm_aliases=comm_aliases
                    )
                )

        # Outcome anomaly detection (correlates session result with token usage)
        if token_usage:
            outcome_anomalies = detect_outcome_anomalies(
                token_usage, subtype, has_thinking_only_turn=has_thinking_only_turn
            )
            anomalies.extend(outcome_anomalies)

        # API retry exhaustion anomaly — fires regardless of token_usage presence
        if api_retry_exhausted:
            from autoskillit.execution.anomaly_detection import (
                OUTCOME_ANOMALY_PID_SENTINEL,
                OUTCOME_ANOMALY_SEQ_SENTINEL,
                AnomalyKind,
                AnomalySeverity,
            )

            anomalies.append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "seq": OUTCOME_ANOMALY_SEQ_SENTINEL,
                    "event": "anomaly",
                    "kind": str(AnomalyKind.API_RETRY_EXHAUSTION),
                    "severity": str(AnomalySeverity.WARNING),
                    "pid": OUTCOME_ANOMALY_PID_SENTINEL,
                    "detail": {"subtype": subtype, "api_retry_count": api_retry_count},
                    "snapshot": {},
                }
            )

        if ndjson_unknown_event_count > 0 or ndjson_unknown_item_count > 0:
            from autoskillit.execution.anomaly_detection import (
                OUTCOME_ANOMALY_PID_SENTINEL,
                OUTCOME_ANOMALY_SEQ_SENTINEL,
                AnomalyKind,
                AnomalySeverity,
            )

            anomalies.append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "seq": OUTCOME_ANOMALY_SEQ_SENTINEL,
                    "event": "anomaly",
                    "kind": str(AnomalyKind.NDJSON_DRIFT),
                    "severity": str(AnomalySeverity.WARNING),
                    "pid": OUTCOME_ANOMALY_PID_SENTINEL,
                    "detail": {
                        "ndjson_unknown_event_count": ndjson_unknown_event_count,
                        "ndjson_unknown_item_count": ndjson_unknown_item_count,
                    },
                    "snapshot": {},
                }
            )

        effective_model_id = model_identity.effective_model or _primary_model_identifier(
            token_usage
        )
        _observed = _primary_model_identifier(token_usage) if token_usage else ""
        anomalies.extend(
            detect_model_drift(
                model_identity.configured_model,
                _observed,
                profile_name=model_identity.profile_name,
            )
        )
        if model_identity.profile_name and model_identity.profile_name != "anthropic":
            if effective_model_id.startswith("claude-") or effective_model_id in (
                "sonnet",
                "opus",
                "haiku",
            ):
                logger.warning(
                    "model_identity_mismatch",
                    effective_model_id=effective_model_id,
                    profile_name=model_identity.profile_name,
                    configured_model=model_identity.configured_model,
                )

        if anomalies and publish_artifacts:
            anomalies_path = session_dir / "anomalies.jsonl"
            with anomalies_path.open("w") as f:
                for a in anomalies:
                    f.write(_fast_dumps(a, sort_keys=True) + "\n")

        anomaly_count = len(anomalies)

        duration_seconds: float | None = None
        if elapsed_seconds is not None:
            duration_seconds = elapsed_seconds
        elif end_ts:
            try:
                duration_seconds = max(
                    0.0,
                    (
                        datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
                    ).total_seconds(),
                )
            except ValueError:
                pass

        # Write github_api_usage.json from pre-computed telemetry bundle
        github_api_requests = telemetry.github_api_requests
        execution_identity = telemetry.execution_identity
        if telemetry.github_api_usage is not None and publish_artifacts:
            atomic_write(
                session_dir / "github_api_usage.json",
                _fast_dumps(telemetry.github_api_usage, sort_keys=True, indent=True) + "\n",
            )

        native_shell_capture_projection = (
            native_shell_capture.to_dict(stage="exit")
            if native_shell_capture is not None
            else None
        )

        summary = {
            "session_id": session_id,
            "dir_name": dir_name,
            "pid": pid,
            "cwd": cwd,
            "claude_code_log": cc_log_str,
            "codex_log": _codex_log_str,
            "skill_command": skill_command,
            "success": success,
            "subtype": subtype,
            "cli_subtype": cli_subtype,
            "exit_code": exit_code,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_seconds": duration_seconds,
            "silent_gap_seconds": silent_gap_seconds,
            "snapshot_interval_seconds": snapshot_interval_seconds,
            "snapshot_count": snapshot_count,
            "anomaly_count": anomaly_count,
            "peak_rss_kb": peak_rss_kb,
            "peak_oom_score": peak_oom_score,
            "peak_fd_ratio": round(peak_fd_ratio, 3),
            "termination_reason": termination_reason,
            "kill_reason": kill_reason,
            "provider_used": provider_outcome.provider_used,
            "provider_fallback": provider_outcome.fallback_activated,
            "write_path_warnings": effective_write_path_warnings,
            "write_call_count": write_call_count,
            "fs_writes_detected": fs_writes_detected,
            "git_writes_detected": git_writes_detected,
            "file_changes_count": file_changes_count,
            "clone_contamination_reverted": clone_contamination_reverted,
            # Tracer target resolution fields (issue #806)
            "tracked_comm": _effective_tracked_comm,
            "tracked_comm_drift": _tracked_comm_drift,
            "tracer_target_resolution_version": 2,
            "backend": backend,
            "backend_authority": dict(backend_authority)
            if backend_authority is not None
            else None,
            "launch_contract_digest": launch_contract_digest,
            "execution_identity": execution_identity.to_dict(),
            "orphaned_tool_result": orphaned_tool_result,
            "last_stop_reason": last_stop_reason,
            "request_ids": _cb_request_ids,
            "turn_timestamps": _cb_turn_timestamps,
            "turn_tool_calls": _cb_turn_tool_calls,
            "campaign_id": campaign_id,
            "dispatch_id": dispatch_id,
            "github_api_requests": github_api_requests,
            "caller_session_id": caller_session_id,
            "api_retry_count": api_retry_count,
            "api_retry_last_error": api_retry_last_error,
            "api_retry_last_status": api_retry_last_status,
            "api_retry_exhausted": api_retry_exhausted,
            "ndjson_unknown_event_count": ndjson_unknown_event_count,
            "ndjson_unknown_item_count": ndjson_unknown_item_count,
            "native_shell_capture": native_shell_capture_projection,
        }
        if versions is not None:
            summary["versions"] = {
                **versions,
                "model_identifier": effective_model_id,
            }
        if recipe_identity.name or recipe_identity.content_hash:
            summary["recipe_provenance"] = {
                "schema_version": 1,
                "name": recipe_identity.name,
                "version": recipe_identity.version,
                "content_hash": recipe_identity.content_hash,
                "composite_hash": recipe_identity.composite_hash,
            }
        if campaign_id and publish_artifacts:
            meta_path = session_dir / "meta.json"
            atomic_write(
                meta_path,
                _fast_dumps(
                    {"campaign_id": campaign_id, "dispatch_id": dispatch_id}, sort_keys=True
                ),
            )

        if not success and raw_stdout and publish_artifacts:
            atomic_write(session_dir / "raw_stdout.jsonl", raw_stdout)

        if exception_text and publish_artifacts:
            atomic_write(session_dir / "crash_exception.txt", exception_text)

        # Write per-session telemetry files; gate on data presence, not session identity
        label = _resolve_session_label(step_name, dispatch_id)
        if token_usage is not None and publish_artifacts:
            _cw_raw = token_usage.get("cache_write_tokens")
            _cache_write = (
                _cw_raw
                if _cw_raw is not None
                else (token_usage.get("cache_creation_input_tokens") or 0)
            )
            _cr_raw = token_usage.get("cache_read_tokens")
            _cache_read = (
                _cr_raw
                if _cr_raw is not None
                else (token_usage.get("cache_read_input_tokens") or 0)
            )
            tu_data = {
                "session_label": label,
                "input_tokens": token_usage.get("input_tokens") or 0,
                "output_tokens": token_usage.get("output_tokens") or 0,
                "cache_write_tokens": _cache_write,
                "cache_read_tokens": _cache_read,
                "timing_seconds": timing_seconds if timing_seconds is not None else 0.0,
                "order_id": order_id,
                "loc_insertions": loc_insertions,
                "loc_deletions": loc_deletions,
                "peak_context": token_usage.get("peak_context", 0),
                "turn_count": token_usage.get("turn_count", 0),
                "provider_used": provider_outcome.provider_used,
                "model_identifier": effective_model_id,
                "configured_model": model_identity.configured_model,
                "profile_name": model_identity.profile_name,
                "dispatch_id": dispatch_id,
                "campaign_id": campaign_id,
            }
            write_versioned_json(session_dir / "token_usage.json", tu_data, schema_version=2)

        if timing_seconds is not None and publish_artifacts:
            atomic_write(
                session_dir / "step_timing.json",
                _fast_dumps(
                    {
                        "step_name": label,
                        "total_seconds": max(0.0, timing_seconds),
                        "order_id": order_id,
                    }
                ),
            )

        if step_name and audit_record is not None and publish_artifacts:
            atomic_write(session_dir / "audit_log.json", _fast_dumps([audit_record]))

        if publish_artifacts:
            atomic_write(summary_path, _fast_dumps(summary, sort_keys=True, indent=True) + "\n")

        index_entry = {
            "session_id": session_id,
            "dir_name": dir_name,
            "timestamp": start_ts,
            "cwd": cwd,
            "kitchen_id": kitchen_id,
            "order_id": order_id,
            "campaign_id": campaign_id,
            "dispatch_id": dispatch_id,
            "claude_code_log": cc_log_str,
            "codex_log": _codex_log_str,
            "skill_command": skill_command,
            "success": success,
            "subtype": subtype,
            "cli_subtype": cli_subtype,
            "exit_code": exit_code,
            "snapshot_count": snapshot_count,
            "anomaly_count": anomaly_count,
            "peak_rss_kb": peak_rss_kb,
            "peak_oom_score": peak_oom_score,
            "step_name": step_name,
            "input_tokens": (token_usage.get("input_tokens") or 0) if token_usage else 0,
            "output_tokens": (token_usage.get("output_tokens") or 0) if token_usage else 0,
            "cache_write_tokens": (token_usage.get("cache_write_tokens") or 0)
            if token_usage
            else 0,
            "cache_read_tokens": (token_usage.get("cache_read_tokens") or 0) if token_usage else 0,
            "write_call_count": write_call_count,
            "fs_writes_detected": fs_writes_detected,
            "git_writes_detected": git_writes_detected,
            "file_changes_count": file_changes_count,
            "tracked_comm": _effective_tracked_comm,
            "tracked_comm_drift": _tracked_comm_drift,
            "backend": backend,
            "backend_authority": dict(backend_authority)
            if backend_authority is not None
            else None,
            "launch_contract_digest": launch_contract_digest,
            "requested_parent_backend": execution_identity.requested_parent_backend,
            "effective_parent_backend": execution_identity.effective_parent_backend,
            "requested_parent_model": execution_identity.requested_parent_model,
            "effective_parent_model": execution_identity.effective_parent_model,
            "requested_parent_effort": execution_identity.requested_parent_effort,
            "effective_parent_effort": execution_identity.effective_parent_effort,
            "execution_cli_version": execution_identity.cli_version,
            "backend_override_tier": execution_identity.override_tier,
            "backend_override_key_path": execution_identity.override_key_path,
            "parent_session_id": execution_identity.parent_session_id,
            "child_executions": [child.to_dict() for child in execution_identity.children],
            "autoskillit_version": versions.get("autoskillit_version", "") if versions else "",
            "claude_code_version": versions.get("claude_code_version", "") if versions else "",
            "codex_version": versions.get("codex_version", "") if versions else "",
            "recipe_name": recipe_identity.name,
            "recipe_content_hash": recipe_identity.content_hash,
            "recipe_composite_hash": recipe_identity.composite_hash,
            "recipe_version": recipe_identity.version,
            "duration_seconds": duration_seconds,
            "github_api_requests": github_api_requests,
            "provider_used": provider_outcome.provider_used,
            "provider_fallback": provider_outcome.fallback_activated,
            "caller_session_id": caller_session_id,
            "api_retry_count": api_retry_count,
            "api_retry_exhausted": api_retry_exhausted,
            "api_retry_last_error": api_retry_last_error,
            "api_retry_last_status": api_retry_last_status,
            "ndjson_unknown_event_count": ndjson_unknown_event_count,
            "ndjson_unknown_item_count": ndjson_unknown_item_count,
            "outcome_fields": outcome_fields,
            "outcome_invariant_violated": outcome_invariant_violated,
            "outcome_qualifier": outcome_qualifier,
            "native_shell_capture": native_shell_capture_projection,
            "model_identifier": effective_model_id,
            "configured_model": model_identity.configured_model,
            "profile_name": model_identity.profile_name,
            "schema_version": 7,
        }
        index_path = log_root / "sessions.jsonl"
        index_rows = [
            row for row in _read_index_rows(index_path) if row.get("dir_name") != dir_name
        ]
        index_rows.append(index_entry)

        committed_dirs = sorted(
            (
                candidate
                for candidate in sessions_dir.iterdir()
                if candidate.is_dir() and (candidate / "summary.json").is_file()
            ),
            key=lambda candidate: candidate.stat().st_mtime,
        )
        effective_max_sessions = max_sessions if max_sessions is not None else _MAX_SESSIONS
        expired = committed_dirs[: max(0, len(committed_dirs) - effective_max_sessions)]
        surviving_names = {candidate.name for candidate in committed_dirs[len(expired) :]}
        protected_ids = (
            build_protected_campaign_ids(Path(project_dir))
            if project_dir and build_protected_campaign_ids is not None
            else frozenset()
        )
        for candidate in expired:
            if reuse_committed_recovery and candidate.name == dir_name:
                surviving_names.add(candidate.name)
                continue
            if protected_ids:
                try:
                    meta = json.loads((candidate / "meta.json").read_text(encoding="utf-8"))
                except FileNotFoundError:
                    meta = {}
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "session_retention_meta_read_failed",
                        path=candidate,
                        error=str(exc),
                        exc_info=True,
                    )
                    meta = {}
                if meta.get("campaign_id") in protected_ids:
                    surviving_names.add(candidate.name)
                    continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                logger.warning("session_retention_delete_failed", path=candidate, exc_info=True)
                surviving_names.add(candidate.name)

        retained_rows = [row for row in index_rows if row.get("dir_name") in surviving_names]
        atomic_write(
            index_path,
            "".join(_fast_dumps(row, sort_keys=True) + "\n" for row in retained_rows),
        )

    # Co-write session provenance outside the session-index transaction.
    if cwd:
        from autoskillit.core import ProvenanceRecord, write_provenance_record

        write_provenance_record(
            ProvenanceRecord(
                session_id=session_id,
                caller_session_id=caller_session_id,
                kitchen_id=kitchen_id,
                dispatch_id=dispatch_id,
                recipe_name=recipe_identity.name,
                step_name=step_name,
                timestamp=start_ts or end_ts or "",
            ),
            project_dir=Path(project_dir) if project_dir else None,
        )
