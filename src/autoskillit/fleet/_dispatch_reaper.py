"""Stale dispatch process reaping — reap_stale_dispatches(), reap_stale_dispatches_async()."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from autoskillit.core import default_log_dir, get_logger
from autoskillit.execution import kill_process_tree, read_boot_id, read_starttime_ticks

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignStateMutator, DispatchRecord

logger = get_logger(__name__)


def _append_reaper_event(dispatch: DispatchRecord, reason: str, reaper_dispatch_id: str) -> None:
    log_path = default_log_dir() / "reaper_events.jsonl"
    event = {
        "ts": time.time(),
        "action": reason,
        "victim_dispatch_id": dispatch.dispatch_id,
        "victim_pid": dispatch.dispatched_pid,
        "victim_session_id": dispatch.dispatched_session_id,
        "reaper_dispatch_id": reaper_dispatch_id,
        "campaign_id": dispatch.campaign_id,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        logger.warning("reaper: failed to append reaper event", exc_info=True)


def _apply_stale_dispatch(
    dispatch: DispatchRecord,
    reason: str,
    m: CampaignStateMutator,
    reaper_dispatch_id: str = "",
) -> None:
    from autoskillit.fleet import classify_stale_dispatch  # noqa: PLC0415

    new_status, sidecar = classify_stale_dispatch(dispatch)
    dispatch.status = new_status
    dispatch.reason = reason
    if sidecar:
        dispatch.sidecar_path = sidecar
    dispatch.ended_at = time.time()

    dispatch.reaper_reason = reason
    dispatch.reaper_dispatch_id = reaper_dispatch_id

    if dispatch.dispatched_session_log_dir:
        tombstone_dir = Path(dispatch.dispatched_session_log_dir)
        if tombstone_dir.exists():
            tombstone = {
                "ts": dispatch.ended_at,
                "action": reason,
                "reaper_dispatch_id": reaper_dispatch_id,
                "victim_pid": dispatch.dispatched_pid,
                "victim_dispatch_id": dispatch.dispatch_id,
                "campaign_id": dispatch.campaign_id,
            }
            try:
                with (tombstone_dir / "reaper_action.json").open("w", encoding="utf-8") as f:
                    f.write(json.dumps(tombstone))
            except OSError:
                logger.warning("reaper: failed to write tombstone", exc_info=True)

    _append_reaper_event(dispatch, reason, reaper_dispatch_id)
    m.mark_dirty()


def _mark_dead_pid(
    dry_run: bool,
    name: str,
    pid: int,
    dispatch: DispatchRecord,
    m: CampaignStateMutator,
    reaper_dispatch_id: str = "",
) -> None:
    if dry_run:
        logger.info("reap: [WOULD MARK]  %s  pid=%d  (process dead)", name, pid)
    else:
        _apply_stale_dispatch(dispatch, "reaped_dead_pid", m, reaper_dispatch_id)
        logger.info("reap: [MARKED]      %s  pid=%d  (process dead)", name, pid)


def _is_dispatch_heartbeating(
    dispatches_dir: Path,
    dispatch_id: str,
    grace_seconds: float = 90.0,
) -> bool:
    """Return True if the dispatch has a fresh heartbeat file.

    A fresh heartbeat means the file exists and its mtime is within ``grace_seconds``
    of the current time, indicating the dispatch is actively executing.
    """
    hb_path = dispatches_dir / f"dispatch-{dispatch_id}.heartbeat"
    try:
        mtime = hb_path.stat().st_mtime
        return time.time() - mtime <= grace_seconds
    except OSError:
        return False


def reap_stale_dispatches(
    state_path: Path,
    *,
    dry_run: bool = False,
    skip_dispatch_ids: frozenset[str] | None = None,
    own_campaign_id: str | None = None,
    min_reap_age_seconds: float = 60.0,
    reaper_dispatch_id: str = "",
    heartbeat_grace_seconds: float = 90.0,
) -> None:
    """Reap stale RUNNING dispatches with PID-recycling-safe identity checks.

    Uses CampaignStateMutator (which holds _resume_lock + flock on .lock sidecar)
    to protect against concurrent reap and resume invocations.
    For each RUNNING dispatch:
    - Boot-ID mismatch -> reaped_pid_recycled (no kill)
    - Process dead -> reaped_dead_pid
    - Process alive + ticks match -> kill + reaped_orphan
    - Process alive + ticks mismatch -> reaped_pid_recycled (no kill)
    """
    from autoskillit.fleet import (  # noqa: PLC0415
        CampaignStateMutator,
        DispatchStatus,
    )

    current_boot_id = read_boot_id()

    if not state_path.exists():
        logger.info("reap: state file not found, nothing to reap: %s", state_path)
        return

    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            logger.warning("reap: cannot read state file: %s", state_path)
            return

        running = [d for d in m.state.dispatches if d.status == DispatchStatus.RUNNING]
        if not running:
            logger.info("reap: no running dispatches in campaign %s", m.state.campaign_id)
            return

        if own_campaign_id and m.state.campaign_id == own_campaign_id:
            logger.info(
                "reap: [SKIPPED]     campaign %s  (own campaign, %d running siblings)",
                m.state.campaign_id,
                len(running),
            )
            return

        logger.info(
            "reap: scanning %d dispatches in campaign %s", len(running), m.state.campaign_id
        )

        for dispatch in running:
            name = dispatch.name
            if skip_dispatch_ids and dispatch.dispatch_id in skip_dispatch_ids:
                logger.info(
                    "reap: [SKIPPED]     %s  dispatch_id=%s  (self-exclusion)",
                    name,
                    dispatch.dispatch_id,
                )
                continue
            pid = dispatch.dispatched_pid

            age = time.time() - dispatch.started_at if dispatch.started_at > 0 else float("inf")
            if age < min_reap_age_seconds:
                logger.info(
                    "reap: [SKIPPED]     %s  dispatch_id=%s  (too young, age=%.1fs)",
                    name,
                    dispatch.dispatch_id,
                    age,
                )
                continue

            if pid == 0:
                if dry_run:
                    logger.info("reap: [WOULD MARK]  %s  pid=0  (no PID recorded)", name)
                else:
                    _apply_stale_dispatch(dispatch, "reaped_dead_pid", m, reaper_dispatch_id)
                    logger.info("reap: [MARKED]      %s  (no PID recorded)", name)
                continue

            if (
                dispatch.dispatched_boot_id != ""
                and current_boot_id is not None
                and dispatch.dispatched_boot_id != current_boot_id
            ):
                if dry_run:
                    logger.info(
                        "reap: [WOULD MARK]  %s  pid=%d  (rebooted, pid_recycled)", name, pid
                    )
                else:
                    _apply_stale_dispatch(dispatch, "reaped_pid_recycled", m, reaper_dispatch_id)
                    logger.info(
                        "reap: [MARKED]      %s  pid=%d  (rebooted, pid_recycled)",
                        name,
                        pid,
                    )
                continue

            if not psutil.pid_exists(pid):
                _mark_dead_pid(dry_run, name, pid, dispatch, m, reaper_dispatch_id)
                continue

            current_ticks = read_starttime_ticks(pid)
            if current_ticks is not None and current_ticks == dispatch.dispatched_starttime_ticks:
                identity_confirmed = True
            elif current_ticks is None and dispatch.dispatched_create_time > 0.0:
                try:
                    actual_ct = psutil.Process(pid).create_time()
                    identity_confirmed = abs(actual_ct - dispatch.dispatched_create_time) < 1.0
                except psutil.NoSuchProcess:
                    _mark_dead_pid(dry_run, name, pid, dispatch, m, reaper_dispatch_id)
                    continue
                except psutil.AccessDenied:
                    identity_confirmed = False
            elif (
                dispatch.dispatched_starttime_ticks == 0 and dispatch.dispatched_create_time > 0.0
            ):
                try:
                    actual_ct = psutil.Process(pid).create_time()
                    identity_confirmed = abs(actual_ct - dispatch.dispatched_create_time) < 1.0
                    logger.info("reap: ticks=0 fallback to create_time for %s pid=%d", name, pid)
                except psutil.NoSuchProcess:
                    _mark_dead_pid(dry_run, name, pid, dispatch, m, reaper_dispatch_id)
                    continue
                except psutil.AccessDenied:
                    identity_confirmed = False
            else:
                identity_confirmed = False

            if identity_confirmed:
                if _is_dispatch_heartbeating(
                    state_path.parent, dispatch.dispatch_id, heartbeat_grace_seconds
                ):
                    logger.info(
                        "reap: [SKIPPED]     %s  dispatch_id=%s  (dispatch heartbeat active)",
                        name,
                        dispatch.dispatch_id,
                    )
                    continue
                if dry_run:
                    logger.info(
                        "reap: [WOULD KILL]  %s  pid=%d  (orphan, identity match)", name, pid
                    )
                else:
                    try:
                        kill_process_tree(pid)
                    except Exception:
                        logger.warning(
                            "reap: kill_process_tree failed for pid=%d", pid, exc_info=True
                        )
                    _apply_stale_dispatch(dispatch, "reaped_orphan", m, reaper_dispatch_id)
                    logger.info("reap: [KILLED]      %s  pid=%d  (orphan reaped)", name, pid)
            else:
                if dry_run:
                    logger.info(
                        "reap: [WOULD MARK]  %s  pid=%d  (PID recycled, no kill)", name, pid
                    )
                else:
                    _apply_stale_dispatch(dispatch, "reaped_pid_recycled", m, reaper_dispatch_id)
                    logger.info(
                        "reap: [MARKED]      %s  pid=%d  (PID recycled, no kill)",
                        name,
                        pid,
                    )


async def reap_stale_dispatches_async(
    state_paths: list[Path],
    *,
    skip_dispatch_ids: frozenset[str] | None = None,
    own_campaign_id: str | None = None,
    min_reap_age_seconds: float = 60.0,
    reaper_dispatch_id: str = "",
    heartbeat_grace_seconds: float = 90.0,
) -> None:
    import functools  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    for sp in state_paths:
        await loop.run_in_executor(
            None,
            functools.partial(
                reap_stale_dispatches,
                sp,
                skip_dispatch_ids=skip_dispatch_ids,
                own_campaign_id=own_campaign_id,
                min_reap_age_seconds=min_reap_age_seconds,
                reaper_dispatch_id=reaper_dispatch_id,
                heartbeat_grace_seconds=heartbeat_grace_seconds,
            ),
        )
