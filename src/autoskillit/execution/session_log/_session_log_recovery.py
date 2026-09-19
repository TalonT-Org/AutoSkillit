"""Crash recovery scanner for SIGKILL'd headless sessions."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import (
    CampaignProtector,
    FaultDomain,
    InfraExitCategory,
    InfraOutcome,
    RetryReason,
    get_logger,
    is_pid_zombie,
    read_boot_id,
    read_starttime_ticks,
)
from autoskillit.execution.evidence.linux_tracing import TraceEnrollmentRecord, read_enrollment
from autoskillit.execution.session_log._session_retention import (
    prune_execution_candidate_manifests_at_root,
)
from autoskillit.execution.session_log.session_log import flush_session_log, resolve_log_dir

logger = get_logger(__name__)


def _delete_trace_pair(trace_file: Path, enrollment_path: Path) -> None:
    """Remove a trace and its matching enrollment sidecar together."""
    trace_file.unlink(missing_ok=True)
    enrollment_path.unlink(missing_ok=True)


def _eligible_enrolled_trace(
    trace_file: Path,
    tmpfs: Path,
    current_boot_id: str | None,
) -> tuple[int, Path, TraceEnrollmentRecord] | None:
    """Return a dead, enrolled trace that is old enough for crash recovery."""
    try:
        age_seconds = time.time() - trace_file.stat().st_mtime
    except OSError:
        return None
    if age_seconds < 30:
        return None

    try:
        pid = int(trace_file.stem.split("_")[-1])
    except (ValueError, IndexError):
        pid = -1

    enrollment_path = tmpfs / f"autoskillit_enrollment_{pid}.json"
    enrollment = read_enrollment(enrollment_path)
    if enrollment is None:
        logger.debug("Skipping %s: no enrollment sidecar", trace_file.name)
        return None

    if current_boot_id and enrollment.boot_id and enrollment.boot_id != current_boot_id:
        logger.debug("Skipping %s: boot_id mismatch", trace_file.name)
        _delete_trace_pair(trace_file, enrollment_path)
        return None

    current_ticks = read_starttime_ticks(pid)
    if (
        current_ticks is not None
        and current_ticks == enrollment.starttime_ticks
        and not is_pid_zombie(pid)
    ):
        logger.debug("Skipping %s: PID %d still alive", trace_file.name, pid)
        return None
    return pid, enrollment_path, enrollment


def _decode_enrolled_trace(
    trace_file: Path,
    enrollment_path: Path,
    enrollment: TraceEnrollmentRecord,
) -> list[dict[str, object]] | None:
    """Decode a dead trace, removing only permanently invalid or alien evidence."""
    snapshots: list[dict[str, object]] = []
    corrupt_reason: str | None = None
    try:
        for line in trace_file.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                snapshot = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                corrupt_reason = "invalid JSON"
                break
            if not isinstance(snapshot, dict):
                corrupt_reason = "non-object JSON"
                break
            snapshots.append(snapshot)
    except UnicodeDecodeError:
        corrupt_reason = "non-UTF-8 content"
    except OSError:
        return None

    if corrupt_reason is not None:
        logger.warning(
            "recover_crashed_sessions_permanently_corrupt_trace",
            trace_path=str(trace_file),
            reason=corrupt_reason,
        )
        _delete_trace_pair(trace_file, enrollment_path)
        return None

    if snapshots and enrollment.comm:
        first_comm = snapshots[0].get("comm", "")
        if first_comm and isinstance(first_comm, str) and first_comm != enrollment.comm:
            logger.debug(
                "Skipping %s: alien comm '%s' (expected '%s')",
                trace_file.name,
                first_comm,
                enrollment.comm,
            )
            _delete_trace_pair(trace_file, enrollment_path)
            return None
    return snapshots


def _finalize_crashed_trace(
    *,
    trace_file: Path,
    log_dir: str,
    project_dir: str,
    max_sessions: int | None,
    build_protected_campaign_ids: CampaignProtector | None,
    pid: int,
    snapshots: list[dict[str, object]],
) -> bool:
    """Write one decoded crash trace into the durable session log."""
    try:
        mtime_ts = datetime.fromtimestamp(trace_file.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return False

    try:
        from autoskillit.core import ProviderOutcome, RecipeIdentity, SessionTelemetry

        flush_session_log(
            log_dir=log_dir,
            cwd="",
            session_id=f"crashed_{pid}_{mtime_ts.replace(':', '-')}",
            pid=pid,
            skill_command="",
            success=False,
            needs_retry=False,
            retry_reason=RetryReason.NONE.value,
            infra=InfraOutcome(
                exit_category=InfraExitCategory.UNCLASSIFIED.value,
                cleanup_incomplete=False,
                fault_domain=FaultDomain.INFRASTRUCTURE,
            ),
            api_error_status=None,
            is_error=True,
            subtype="crashed",
            exit_code=-1,
            start_ts=mtime_ts,
            proc_snapshots=snapshots if snapshots else None,
            termination_reason="CRASHED",
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
            telemetry=SessionTelemetry.empty(),
            project_dir=project_dir,
            max_sessions=max_sessions,
            build_protected_campaign_ids=build_protected_campaign_ids,
            is_crash_recovery=True,
        )
    except Exception:
        logger.warning(
            "recover_crashed_sessions_finalize_failed",
            trace_path=str(trace_file),
            exc_info=True,
        )
        return False
    return True


def recover_crashed_sessions(
    tmpfs_path: str = "/dev/shm",
    log_dir: str = "",
    project_dir: str = "",
    max_sessions: int | None = None,
    build_protected_campaign_ids: CampaignProtector | None = None,
) -> int:
    """Scan tmpfs for orphaned trace files from SIGKILL'd sessions and finalize them.

    Returns the number of sessions recovered.

    Also runs an independent child-outcome snapshot reconciliation pass
    (issue #4623), unconditionally and before the tmpfs trace-file gates
    below — canonical snapshots are logged directly by the hook observer and
    have no tmpfs trace of their own, so they must not depend on enrollment.
    A reconciliation failure never blocks trace-file recovery.
    """
    try:
        # Deferred: autoskillit.execution.child_outcomes imports
        # autoskillit.execution.session_log.session_log at module level, which
        # would otherwise make this module-level import a circular import
        # through the session_log/ gateway (execution.session_log ->
        # _session_log_recovery -> child_outcomes -> session_log.session_log).
        from autoskillit.execution.child_outcomes import reconcile_child_outcome_snapshots

        reconcile_child_outcome_snapshots(resolve_log_dir(log_dir))
    except Exception:
        logger.debug("child_outcome_snapshot_reconciliation_failed", exc_info=True)

    try:
        log_root = resolve_log_dir(log_dir)
        if (log_root / "execution-candidates").is_dir():
            protected_ids = (
                build_protected_campaign_ids(Path(project_dir))
                if project_dir and build_protected_campaign_ids is not None
                else frozenset()
            )
            prune_execution_candidate_manifests_at_root(
                log_root,
                max_sessions=max_sessions,
                protected_ids=protected_ids,
            )
    except Exception:
        logger.debug("execution_candidate_manifest_retention_failed", exc_info=True)

    tmpfs = Path(tmpfs_path)
    if not tmpfs.is_dir():
        return 0

    count = 0
    current_boot_id = read_boot_id()
    for trace_file in sorted(tmpfs.glob("autoskillit_trace_*.jsonl")):
        candidate = _eligible_enrolled_trace(trace_file, tmpfs, current_boot_id)
        if candidate is not None:
            pid, enrollment_path, enrollment = candidate
            snapshots = _decode_enrolled_trace(trace_file, enrollment_path, enrollment)
            if snapshots is not None and _finalize_crashed_trace(
                trace_file=trace_file,
                log_dir=log_dir,
                project_dir=project_dir,
                max_sessions=max_sessions,
                build_protected_campaign_ids=build_protected_campaign_ids,
                pid=pid,
                snapshots=snapshots,
            ):
                _delete_trace_pair(trace_file, enrollment_path)
                count += 1

    return count
