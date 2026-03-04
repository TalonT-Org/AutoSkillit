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
from pathlib import Path

from autoskillit.core import get_logger
from autoskillit.execution.anomaly_detection import detect_anomalies

logger = get_logger(__name__)

_MAX_SESSIONS = 500


def resolve_log_dir(log_dir: str) -> Path:
    """Resolve session log directory. Empty string = platform default."""
    if log_dir:
        return Path(log_dir).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "autoskillit" / "logs"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "autoskillit" / "logs"


def flush_session_log(
    *,
    log_dir: str,
    cwd: str,
    session_id: str,
    pid: int,
    skill_command: str,
    success: bool,
    subtype: str,
    exit_code: int,
    start_ts: str,
    proc_snapshots: list[dict[str, object]] | None,
) -> None:
    """Flush session diagnostics to disk.

    Writes proc_trace.jsonl, summary.json, anomalies.jsonl (if any),
    and appends to the global sessions.jsonl index. Applies retention
    to keep at most 500 session directories.
    """
    log_root = resolve_log_dir(log_dir)
    dir_name = session_id if session_id else f"pid_{pid}_{start_ts}"
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)

    snapshot_count = 0
    peak_rss_kb = 0
    peak_oom_score = 0
    peak_fd_ratio = 0.0
    anomalies: list[dict[str, object]] = []

    # Write proc_trace.jsonl
    if proc_snapshots:
        snapshot_count = len(proc_snapshots)
        trace_path = session_dir / "proc_trace.jsonl"
        with trace_path.open("w") as f:
            for seq, snap in enumerate(proc_snapshots):
                record = {
                    "ts": start_ts,
                    "seq": seq,
                    "event": "snapshot",
                    "pid": pid,
                    **snap,
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

        # Anomaly detection
        anomalies = detect_anomalies(proc_snapshots, pid)

    # Write anomalies.jsonl (only if anomalies exist)
    if anomalies:
        anomalies_path = session_dir / "anomalies.jsonl"
        with anomalies_path.open("w") as f:
            for a in anomalies:
                f.write(json.dumps(a, sort_keys=True) + "\n")

    anomaly_count = len(anomalies)

    # Write summary.json
    summary = {
        "session_id": session_id,
        "dir_name": dir_name,
        "pid": pid,
        "cwd": cwd,
        "skill_command": skill_command,
        "success": success,
        "subtype": subtype,
        "exit_code": exit_code,
        "start_ts": start_ts,
        "snapshot_count": snapshot_count,
        "anomaly_count": anomaly_count,
        "peak_rss_kb": peak_rss_kb,
        "peak_oom_score": peak_oom_score,
        "peak_fd_ratio": round(peak_fd_ratio, 3),
    }
    summary_path = session_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")

    # Append to sessions.jsonl index
    index_entry = {
        "session_id": session_id,
        "dir_name": dir_name,
        "timestamp": start_ts,
        "cwd": cwd,
        "skill_command": skill_command[:100],
        "success": success,
        "subtype": subtype,
        "exit_code": exit_code,
        "snapshot_count": snapshot_count,
        "anomaly_count": anomaly_count,
        "peak_rss_kb": peak_rss_kb,
        "peak_oom_score": peak_oom_score,
    }
    index_path = log_root / "sessions.jsonl"
    with index_path.open("a") as f:
        f.write(json.dumps(index_entry, sort_keys=True) + "\n")

    # Retention: keep at most _MAX_SESSIONS session directories
    _enforce_retention(log_root)


def _enforce_retention(log_root: Path) -> None:
    """Delete oldest session directories if count exceeds _MAX_SESSIONS."""
    sessions_dir = log_root / "sessions"
    if not sessions_dir.is_dir():
        return

    dirs = sorted(sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    if len(dirs) <= _MAX_SESSIONS:
        return

    expired = dirs[: len(dirs) - _MAX_SESSIONS]
    surviving_names = {d.name for d in dirs[len(dirs) - _MAX_SESSIONS :]}

    for d in expired:
        shutil.rmtree(d, ignore_errors=True)

    # Rewrite sessions.jsonl to remove expired entries
    index_path = log_root / "sessions.jsonl"
    if index_path.is_file():
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
        index_path.write_text("\n".join(kept) + "\n" if kept else "")
