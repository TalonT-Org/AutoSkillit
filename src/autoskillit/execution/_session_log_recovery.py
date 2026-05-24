"""Crash recovery scanner for SIGKILL'd headless sessions."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from autoskillit.core import get_logger
from autoskillit.execution.linux_tracing import read_boot_id, read_enrollment, read_starttime_ticks
from autoskillit.execution.session_log import flush_session_log

logger = get_logger(__name__)


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
            from autoskillit.core import ProviderOutcome, RecipeIdentity, SessionTelemetry

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
                provider_outcome=ProviderOutcome.none_used(),
                recipe_identity=RecipeIdentity.empty(),
                telemetry=SessionTelemetry.empty(),
            )
        except Exception:
            logger.debug(
                "recover_crashed_sessions: failed to finalize %s", trace_file, exc_info=True
            )
            continue

        trace_file.unlink(missing_ok=True)
        enrollment_path.unlink(missing_ok=True)

        count += 1

    return count
