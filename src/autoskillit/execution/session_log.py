"""File-based session diagnostics log writer.

Writes structured JSON logs to a global, XDG-aware directory. Each headless
session gets its own directory keyed by session ID, containing process trace
data, a session summary, and flagged anomalies. An append-only index file
provides quick scanning across all sessions.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import SessionTelemetry

import psutil

from autoskillit.core import atomic_write, claude_code_log_path, get_logger
from autoskillit.execution.anomaly_detection import (
    detect_anomalies,
    detect_identity_drift,
    detect_outcome_anomalies,
)
from autoskillit.execution.linux_tracing import (
    read_boot_id,
    read_enrollment,
    read_starttime_ticks,
)

logger = get_logger(__name__)

_MAX_SESSIONS = 2000


def _primary_model_identifier(token_usage: dict[str, Any] | None) -> str:
    """Return the model name with the most total tokens from model_breakdown.

    Returns "" when token_usage is absent or model_breakdown is empty.
    """
    if not token_usage:
        return ""
    mb = token_usage.get("model_breakdown", {})
    if not isinstance(mb, dict) or not mb:
        return ""
    return max(mb, key=lambda m: sum(mb[m].values()) if isinstance(mb[m], dict) else 0)


_CLEAR_MARKER_FILENAME = ".telemetry_cleared_at"


def resolve_log_dir(log_dir: str) -> Path:
    """Resolve session log directory. Empty string = platform default."""
    if log_dir:
        return Path(log_dir).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "autoskillit" / "logs"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "autoskillit" / "logs"


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
    order_id: str = "",
    campaign_id: str = "",
    dispatch_id: str = "",
    project_dir: str = "",
    build_protected_campaign_ids: Callable[[Path], frozenset[str]] | None = None,
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
    provider_used: str = "",
    provider_fallback: bool = False,
    snapshot_interval_seconds: float = 0.0,
    step_name: str = "",
    cli_subtype: str = "",
    write_path_warnings: list[str] | None = None,
    write_call_count: int = 0,
    clone_contamination_reverted: bool = False,
    tracked_comm: str | None = None,
    exception_text: str = "",
    orphaned_tool_result: bool = False,
    raw_stdout: str = "",
    last_stop_reason: str = "",
    versions: dict[str, Any] | None = None,
    model_identifier: str = "",
    recipe_name: str = "",
    recipe_content_hash: str = "",
    recipe_composite_hash: str = "",
    recipe_version: str = "",
    max_sessions: int | None = None,
    telemetry: SessionTelemetry,
) -> None:
    """Flush session diagnostics to disk.

    Writes proc_trace.jsonl, summary.json, anomalies.jsonl (if any),
    and appends to the global sessions.jsonl index. Applies retention
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
    dir_name = session_id if session_id else f"no_session_{start_ts.replace(':', '-')}"

    cc_log = claude_code_log_path(cwd, session_id)
    cc_log_str: str | None = str(cc_log) if cc_log else None

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
    _cb_turn_tool_calls: list[list[str]] = []
    if cc_log and cc_log.exists():
        seen_request_ids: set[str] = set()
        try:
            for raw_line in cc_log.read_text(encoding="utf-8", errors="replace").splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                rid = rec.get("requestId", "")
                ts = rec.get("timestamp", "")
                if rid and rid not in seen_request_ids:
                    seen_request_ids.add(rid)
                    _cb_request_ids.append(str(rid))
                    if ts:
                        _cb_turn_timestamps.append(str(ts))
                    _message = rec.get("message")
                    _content = _message.get("content", []) if isinstance(_message, dict) else []
                    _tools = [
                        str(blk["name"])
                        for blk in _content
                        if isinstance(blk, dict)
                        and blk.get("type") == "tool_use"
                        and isinstance(blk.get("name"), str)
                        and blk["name"]
                    ]
                    _cb_turn_tool_calls.append(_tools[:8])
        except OSError:
            logger.debug("channel_b_log_read_error", path=cc_log_str, exc_info=True)

    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)

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
                f.write(json.dumps(record, sort_keys=True) + "\n")

                # Track peaks
                rss = snap.get("vm_rss_kb", 0)
                if isinstance(rss, int) and rss > peak_rss_kb:
                    peak_rss_kb = rss
                oom = snap.get("oom_score", 0)
                if isinstance(oom, int) and oom > peak_oom_score:
                    peak_oom_score = oom
                fd_count = snap.get("fd_count", 0)
                fd_limit = snap.get("fd_soft_limit", 0)
                if isinstance(fd_count, int) and isinstance(fd_limit, int) and fd_limit > 0:
                    ratio = fd_count / fd_limit
                    if ratio > peak_fd_ratio:
                        peak_fd_ratio = ratio

        # Compute effective tracked_comm from snapshots if not provided by caller
        if _effective_tracked_comm is None:
            # Use modal comm value across all snapshots
            comm_counts: dict[str, int] = {}
            for snap in proc_snapshots:
                c = snap.get("comm", "")
                if c and isinstance(c, str):
                    comm_counts[c] = comm_counts.get(c, 0) + 1
            if comm_counts:
                _effective_tracked_comm = max(comm_counts, key=lambda k: comm_counts[k])

        # Detect identity drift: if snapshots have mixed comm values, flag it
        if _effective_tracked_comm:
            comms_seen = {snap.get("comm", "") for snap in proc_snapshots if snap.get("comm", "")}
            if len(comms_seen) > 1:
                _tracked_comm_drift = True

        # Anomaly detection (standard)
        anomalies = detect_anomalies(proc_snapshots, pid)
        # Identity drift anomaly (post-fix immunity check)
        if _effective_tracked_comm:
            drift_anomalies = detect_identity_drift(proc_snapshots, _effective_tracked_comm)
            anomalies.extend(drift_anomalies)

    # Outcome anomaly detection (correlates session result with token usage)
    if token_usage:
        outcome_anomalies = detect_outcome_anomalies(token_usage, subtype)
        anomalies.extend(outcome_anomalies)

    # Write anomalies.jsonl (only if anomalies exist)
    if anomalies:
        anomalies_path = session_dir / "anomalies.jsonl"
        with anomalies_path.open("w") as f:
            for a in anomalies:
                f.write(json.dumps(a, sort_keys=True) + "\n")

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
    if telemetry.github_api_usage is not None:
        atomic_write(
            session_dir / "github_api_usage.json",
            json.dumps(telemetry.github_api_usage, sort_keys=True, indent=2) + "\n",
        )

    # Write summary.json
    summary = {
        "session_id": session_id,
        "dir_name": dir_name,
        "pid": pid,
        "cwd": cwd,
        "claude_code_log": cc_log_str,
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
        "provider_used": provider_used,
        "provider_fallback": provider_fallback,
        "write_path_warnings": effective_write_path_warnings,
        "write_call_count": write_call_count,
        "clone_contamination_reverted": clone_contamination_reverted,
        # Tracer target resolution fields (issue #806)
        "tracked_comm": _effective_tracked_comm,
        "tracked_comm_drift": _tracked_comm_drift,
        "tracer_target_resolution_version": 2,
        "orphaned_tool_result": orphaned_tool_result,
        "last_stop_reason": last_stop_reason,
        "request_ids": _cb_request_ids,
        "turn_timestamps": _cb_turn_timestamps,
        "turn_tool_calls": _cb_turn_tool_calls,
        "campaign_id": campaign_id,
        "dispatch_id": dispatch_id,
        "github_api_requests": github_api_requests,
    }
    if versions is not None:
        effective_model_id = model_identifier or _primary_model_identifier(token_usage)
        summary["versions"] = {
            **versions,
            "model_identifier": effective_model_id,
        }
    if recipe_name or recipe_content_hash:
        summary["recipe_provenance"] = {
            "schema_version": 1,
            "recipe_name": recipe_name,
            "recipe_version": recipe_version,
            "content_hash": recipe_content_hash,
            "composite_hash": recipe_composite_hash,
        }
    summary_path = session_dir / "summary.json"
    atomic_write(summary_path, json.dumps(summary, sort_keys=True, indent=2) + "\n")

    if campaign_id:
        meta_path = session_dir / "meta.json"
        atomic_write(
            meta_path,
            json.dumps({"campaign_id": campaign_id, "dispatch_id": dispatch_id}, sort_keys=True),
        )

    if not success and raw_stdout:
        atomic_write(session_dir / "raw_stdout.jsonl", raw_stdout)

    if exception_text:
        atomic_write(session_dir / "crash_exception.txt", exception_text)

    # Write per-session telemetry files; gate on data presence, not session identity
    label = _resolve_session_label(step_name, dispatch_id)
    if token_usage is not None:
        tu_data = {
            "session_label": label,
            "input_tokens": token_usage.get("input_tokens", 0),
            "output_tokens": token_usage.get("output_tokens", 0),
            "cache_creation_input_tokens": token_usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": token_usage.get("cache_read_input_tokens", 0),
            "timing_seconds": timing_seconds if timing_seconds is not None else 0.0,
            "order_id": order_id,
            "loc_insertions": loc_insertions,
            "loc_deletions": loc_deletions,
            "peak_context": token_usage.get("peak_context", 0),
            "turn_count": token_usage.get("turn_count", 0),
            "provider_used": provider_used,
        }
        atomic_write(session_dir / "token_usage.json", json.dumps(tu_data))

    if timing_seconds is not None:
        atomic_write(
            session_dir / "step_timing.json",
            json.dumps(
                {
                    "step_name": label,
                    "total_seconds": max(0.0, timing_seconds),
                    "order_id": order_id,
                }
            ),
        )

    if step_name and audit_record is not None:
        atomic_write(session_dir / "audit_log.json", json.dumps([audit_record]))

    # Append to sessions.jsonl index
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
        "skill_command": skill_command[:100],
        "success": success,
        "subtype": subtype,
        "cli_subtype": cli_subtype,
        "exit_code": exit_code,
        "snapshot_count": snapshot_count,
        "anomaly_count": anomaly_count,
        "peak_rss_kb": peak_rss_kb,
        "peak_oom_score": peak_oom_score,
        "step_name": step_name,
        "input_tokens": token_usage.get("input_tokens", 0) if token_usage else 0,
        "output_tokens": token_usage.get("output_tokens", 0) if token_usage else 0,
        "cache_creation_input_tokens": token_usage.get("cache_creation_input_tokens", 0)
        if token_usage
        else 0,
        "cache_read_input_tokens": token_usage.get("cache_read_input_tokens", 0)
        if token_usage
        else 0,
        "write_call_count": write_call_count,
        "tracked_comm": _effective_tracked_comm,
        "tracked_comm_drift": _tracked_comm_drift,
        "autoskillit_version": versions.get("autoskillit_version", "") if versions else "",
        "claude_code_version": versions.get("claude_code_version", "") if versions else "",
        "recipe_name": recipe_name,
        "recipe_content_hash": recipe_content_hash,
        "recipe_composite_hash": recipe_composite_hash,
        "recipe_version": recipe_version,
        "duration_seconds": duration_seconds,
        "github_api_requests": github_api_requests,
        "provider_used": provider_used,
        "provider_fallback": provider_fallback,
    }
    index_path = log_root / "sessions.jsonl"
    with index_path.open("a") as f:
        f.write(json.dumps(index_entry, sort_keys=True) + "\n")

    # Retention: keep at most _MAX_SESSIONS session directories
    _enforce_retention(
        log_root,
        project_dir=project_dir,
        build_protected_campaign_ids=build_protected_campaign_ids,
        max_sessions=max_sessions if max_sessions is not None else _MAX_SESSIONS,
    )


def _enforce_retention(
    log_root: Path,
    project_dir: str | None = None,
    build_protected_campaign_ids: Callable[[Path], frozenset[str]] | None = None,
    *,
    max_sessions: int = _MAX_SESSIONS,
) -> None:
    """Delete oldest session directories if count exceeds *max_sessions*.

    When ``project_dir`` is provided, reads fleet state files and ``meta.json``
    sidecars to skip deletion of sessions belonging to active campaigns.
    """
    sessions_dir = log_root / "sessions"
    if not sessions_dir.is_dir():
        return

    dirs = sorted(sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    if len(dirs) <= max_sessions:
        return

    expired = dirs[: len(dirs) - max_sessions]
    surviving_names = {d.name for d in dirs[len(dirs) - max_sessions :]}

    protected_ids = (
        build_protected_campaign_ids(Path(project_dir))
        if project_dir and build_protected_campaign_ids is not None
        else frozenset()
    )

    for d in expired:
        if protected_ids:
            meta_path = d / "meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("campaign_id") in protected_ids:
                        surviving_names.add(d.name)
                        continue
                except (json.JSONDecodeError, OSError):
                    pass
        shutil.rmtree(d, ignore_errors=True)

    # Rewrite sessions.jsonl to remove expired entries
    index_path = log_root / "sessions.jsonl"
    if index_path.is_file():
        # Accepted read-modify-write race: between read_text() below and atomic_write()
        # at the end of this block, a concurrent flush_session_log() call may append a
        # new entry to sessions.jsonl via open("a"). That entry will not be present in
        # `lines`, so it will be silently lost when atomic_write() overwrites the file.
        # Worst case: one diagnostic index entry dropped per concurrent session flush that
        # races this retention sweep. Correctness of the running session is unaffected.
        # File locking (fcntl.flock) is not warranted: the overhead exceeds the value of
        # protecting a best-effort diagnostic artifact.
        lines = index_path.read_text().splitlines()
        kept: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("dir_name") in surviving_names:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
        atomic_write(index_path, "\n".join(kept) + "\n" if kept else "")


def recover_crashed_sessions(tmpfs_path: str = "/dev/shm", log_dir: str = "") -> int:
    """Scan tmpfs for orphaned trace files from SIGKILL'd sessions and finalize them.

    Returns the number of sessions recovered.
    """
    tmpfs = Path(tmpfs_path)
    if not tmpfs.is_dir():
        return 0

    count = 0
    current_boot_id = read_boot_id()
    for trace_file in sorted(tmpfs.glob("autoskillit_trace_*.jsonl")):
        # Skip files modified within the last 30 seconds — may be active
        try:
            age_seconds = time.time() - trace_file.stat().st_mtime
        except OSError:
            continue
        if age_seconds < 30:
            continue

        # Extract PID from filename: autoskillit_trace_{pid}.jsonl
        try:
            pid = int(trace_file.stem.split("_")[-1])
        except (ValueError, IndexError):
            pid = -1

        # Gate 1: Enrollment sidecar must exist — no sidecar means alien/test file
        enrollment_path = tmpfs / f"autoskillit_enrollment_{pid}.json"
        enrollment = read_enrollment(enrollment_path)
        if enrollment is None:
            logger.debug("Skipping %s: no enrollment sidecar", trace_file.name)
            continue

        # Gate 2: Boot ID must match current boot — mismatch means pre-reboot stale file
        if current_boot_id and enrollment.boot_id and enrollment.boot_id != current_boot_id:
            logger.debug("Skipping %s: boot_id mismatch", trace_file.name)
            trace_file.unlink(missing_ok=True)
            enrollment_path.unlink(missing_ok=True)
            continue

        # Gate 3: PID liveness + starttime_ticks identity
        if psutil.pid_exists(pid):
            current_ticks = read_starttime_ticks(pid)
            if current_ticks is not None and current_ticks == enrollment.starttime_ticks:
                logger.debug("Skipping %s: PID %d still alive", trace_file.name, pid)
                continue
            # PID recycled — original process is gone, treat as crash

        # All gates passed — read snapshots and emit crashed row
        snapshots: list[dict[str, object]] = []
        try:
            for line in trace_file.read_text().splitlines():
                try:
                    snapshots.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue

        # Gate 4: comm-based alien file rejection (issue #806 immunity)
        # Use enrollment.comm as the expected comm (schema_version=2 records carry the
        # enrolled binary name). Pre-fix schema_version=1 records have comm="" — skip
        # the check for those to preserve recovery of legitimate crash data.
        _is_alien = False
        expected_comm = enrollment.comm
        if snapshots and expected_comm:
            first_comm = snapshots[0].get("comm", "")
            if first_comm and isinstance(first_comm, str) and first_comm != expected_comm:
                logger.debug(
                    "Skipping %s: alien comm '%s' (expected '%s')",
                    trace_file.name,
                    first_comm,
                    expected_comm,
                )
                _is_alien = True
        if _is_alien:
            # Delete the alien trace — don't leave it to confuse future recovery runs
            trace_file.unlink(missing_ok=True)
            enrollment_path.unlink(missing_ok=True)
            continue

        # Compute start_ts from file mtime
        try:
            mtime_ts = datetime.fromtimestamp(trace_file.stat().st_mtime, tz=UTC).isoformat()
        except OSError:
            continue

        try:
            from autoskillit.core import SessionTelemetry

            flush_session_log(
                log_dir=log_dir,
                cwd="",
                session_id=f"crashed_{pid}_{mtime_ts.replace(':', '-')}",
                pid=pid,
                skill_command="",
                success=False,
                subtype="crashed",
                exit_code=-1,
                start_ts=mtime_ts,
                proc_snapshots=snapshots if snapshots else None,
                termination_reason="CRASHED",
                telemetry=SessionTelemetry.empty(),
            )
        except Exception:
            logger.debug("recover_crashed_sessions: failed to finalize %s", trace_file)
            continue

        trace_file.unlink(missing_ok=True)
        enrollment_path.unlink(missing_ok=True)

        count += 1

    return count
