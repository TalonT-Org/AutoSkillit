"""Readers and derived evidence used by the session-log writer."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from autoskillit.core import get_logger
from autoskillit.execution.evidence.anomaly_detection import (
    detect_anomalies,
    detect_identity_drift,
)

if TYPE_CHECKING:
    from autoskillit.core import SessionLocator

logger = get_logger(__name__)


def _prepare_channel_b_log(
    *,
    session_locator: SessionLocator | None,
    backend: Literal["claude-code", "codex"],
    channel_b_capable: bool,
    cwd: str,
    session_id: str,
    end_ts: str,
) -> tuple[str | None, str | None, float | None, str | None]:
    """Locate Channel-B evidence and read it without affecting log flushing."""
    from autoskillit.execution.backends import CompositeSessionLocator

    codex_log_str: str | None = None
    if session_locator is not None:
        locator = session_locator
    else:
        locator = CompositeSessionLocator().locator_for(backend)

    if channel_b_capable:
        claude_log = locator.session_log_path(cwd, session_id)
        claude_log_str = str(claude_log) if claude_log else None
    else:
        claude_log = None
        claude_log_str = None
        if session_id and not session_id.startswith(("no_session_", "crashed_")):
            try:
                codex_log = locator.locate_session(session_id)
                codex_log_str = str(codex_log) if codex_log else None
            except Exception:
                logger.debug("session_locate_failed", backend=backend, exc_info=True)

    if claude_log and not claude_log.exists():
        logger.warning("claude_code_log_not_found", path=claude_log_str, session_id=session_id)

    silent_gap_seconds: float | None = None
    if claude_log and claude_log.exists() and end_ts:
        try:
            claude_log_mtime = claude_log.stat().st_mtime
            end_dt = datetime.fromisoformat(end_ts)
            silent_gap_seconds = max(0.0, end_dt.timestamp() - claude_log_mtime)
        except (OSError, ValueError):
            pass

    channel_b_text: str | None = None
    if claude_log and claude_log.exists():
        try:
            channel_b_text = claude_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("channel_b_log_read_error", path=claude_log_str, exc_info=True)
    return claude_log_str, codex_log_str, silent_gap_seconds, channel_b_text


def _analyze_proc_snapshots(
    proc_snapshots: list[dict[str, object]],
    pid: int,
    tracked_comm: str | None,
    comm_aliases: frozenset[str],
) -> tuple[int, int, int, float, list[dict[str, object]], str | None, bool]:
    """Derive snapshot metrics, process identity evidence, and anomalies."""
    peak_rss_kb = 0
    peak_oom_score = 0
    peak_fd_ratio = 0.0
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

    effective_tracked_comm = tracked_comm
    if effective_tracked_comm is None:
        comm_counts: dict[str, int] = {}
        for snap in proc_snapshots:
            comm = snap.get("comm", "")
            if comm and isinstance(comm, str):
                comm_counts[comm] = comm_counts.get(comm, 0) + 1
        if comm_counts:
            effective_tracked_comm = max(comm_counts, key=lambda key: comm_counts[key])

    tracked_comm_drift = False
    if effective_tracked_comm:
        comms_seen = {snap.get("comm", "") for snap in proc_snapshots if snap.get("comm", "")}
        if len(comms_seen) > 1:
            tracked_comm_drift = True

    anomalies = detect_anomalies(proc_snapshots, pid)
    if effective_tracked_comm:
        anomalies.extend(
            detect_identity_drift(
                proc_snapshots, effective_tracked_comm, comm_aliases=comm_aliases
            )
        )
    return (
        len(proc_snapshots),
        peak_rss_kb,
        peak_oom_score,
        peak_fd_ratio,
        anomalies,
        effective_tracked_comm,
        tracked_comm_drift,
    )
