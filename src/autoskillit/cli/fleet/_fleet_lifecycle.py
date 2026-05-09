"""Fleet stale-dispatch reaping extracted from _fleet.py."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from autoskillit.core import get_logger

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignStateMutator, DispatchRecord

logger = get_logger(__name__)


def _apply_stale_dispatch(
    dispatch: DispatchRecord,
    reason: str,
    m: CampaignStateMutator,
) -> None:
    from autoskillit.fleet import classify_stale_dispatch  # noqa: PLC0415

    new_status, sidecar = classify_stale_dispatch(dispatch)
    dispatch.status = new_status
    dispatch.reason = reason
    if sidecar:
        dispatch.sidecar_path = sidecar
    dispatch.ended_at = time.time()
    m.mark_dirty()


def _mark_dead_pid(
    dry_run: bool,
    name: str,
    pid: int,
    dispatch: DispatchRecord,
    m: CampaignStateMutator,
) -> None:
    if dry_run:
        logger.info("reap: [WOULD MARK]  %s  pid=%d  (process dead)", name, pid)
    else:
        _apply_stale_dispatch(dispatch, "reaped_dead_pid", m)
        logger.info("reap: [MARKED]      %s  pid=%d  (process dead)", name, pid)


def _reap_stale_dispatches(state_path: Path, *, dry_run: bool = False) -> None:
    """Reap stale RUNNING dispatches with PID-recycling-safe identity checks.

    Uses CampaignStateMutator (which holds _resume_lock + flock on .lock sidecar)
    to protect against concurrent reap and resume invocations.
    For each RUNNING dispatch:
    - Boot-ID mismatch → reaped_pid_recycled (no kill)
    - Process dead → reaped_dead_pid
    - Process alive + ticks match → kill + reaped_orphan
    - Process alive + ticks mismatch → reaped_pid_recycled (no kill)
    """
    from autoskillit.execution import kill_process_tree, read_boot_id, read_starttime_ticks
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

        logger.info(
            "reap: scanning %d dispatches in campaign %s", len(running), m.state.campaign_id
        )

        for dispatch in running:
            name = dispatch.name
            pid = dispatch.dispatched_pid

            if pid == 0:
                if dry_run:
                    logger.info("reap: [WOULD MARK]  %s  pid=0  (no PID recorded)", name)
                else:
                    _apply_stale_dispatch(dispatch, "reaped_dead_pid", m)
                    logger.info("reap: [MARKED]      %s  (no PID recorded)", name)
                continue

            # Boot ID check: if machine rebooted, all PIDs are recycled
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
                    _apply_stale_dispatch(dispatch, "reaped_pid_recycled", m)
                    logger.info(
                        "reap: [MARKED]      %s  pid=%d  (rebooted, pid_recycled)",
                        name,
                        pid,
                    )
                continue

            if not psutil.pid_exists(pid):
                _mark_dead_pid(dry_run, name, pid, dispatch, m)
                continue

            # Process is alive — check identity
            current_ticks = read_starttime_ticks(pid)
            if current_ticks is not None and current_ticks == dispatch.dispatched_starttime_ticks:
                # Linux: /proc identity confirmed
                identity_confirmed = True
            elif current_ticks is None and dispatch.dispatched_create_time > 0.0:
                # Non-Linux fallback: psutil create_time
                try:
                    actual_ct = psutil.Process(pid).create_time()
                    identity_confirmed = abs(actual_ct - dispatch.dispatched_create_time) < 1.0
                except psutil.NoSuchProcess:
                    _mark_dead_pid(dry_run, name, pid, dispatch, m)
                    continue
                except psutil.AccessDenied:
                    identity_confirmed = False
            else:
                identity_confirmed = False

            if identity_confirmed:
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
                    _apply_stale_dispatch(dispatch, "reaped_orphan", m)
                    logger.info("reap: [KILLED]      %s  pid=%d  (orphan reaped)", name, pid)
            else:
                if dry_run:
                    logger.info(
                        "reap: [WOULD MARK]  %s  pid=%d  (PID recycled, no kill)", name, pid
                    )
                else:
                    _apply_stale_dispatch(dispatch, "reaped_pid_recycled", m)
                    logger.info(
                        "reap: [MARKED]      %s  pid=%d  (PID recycled, no kill)",
                        name,
                        pid,
                    )


def _pick_resume_campaign(project_dir: Path) -> tuple[str, str]:
    """Interactively pick a resumable campaign. Returns (campaign_name, campaign_id) or exits."""
    from autoskillit.cli.ui._menu import run_selection_menu  # noqa: PLC0415
    from autoskillit.fleet import TERMINAL_DISPATCH_STATUSES, read_state  # noqa: PLC0415

    fleet_dir = project_dir / ".autoskillit" / "temp" / "fleet"
    active = []
    if fleet_dir.is_dir():
        for subdir in sorted(fleet_dir.iterdir()):
            if not subdir.is_dir():
                continue
            state = read_state(subdir / "state.json")
            if state is None:
                continue
            if any(d.status not in TERMINAL_DISPATCH_STATUSES for d in state.dispatches):
                active.append(state)

    if not active:
        print("No active campaigns to resume.")
        sys.exit(1)

    selected = run_selection_menu(
        active,
        header="Active campaigns (resumable):",
        display_fn=lambda s: f"{s.campaign_name}  [{(s.campaign_id or '')[:8]}…]",
        name_key=lambda s: s.campaign_name,
        timeout=120,
        label="autoskillit fleet campaign --resume",
    )
    if selected is None or isinstance(selected, str):
        print("No campaign selected.")
        sys.exit(1)
    return selected.campaign_name, selected.campaign_id  # type: ignore[union-attr]
