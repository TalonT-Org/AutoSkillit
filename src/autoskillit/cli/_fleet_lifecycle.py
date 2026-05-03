"""Fleet signal guard and stale-dispatch reaping extracted from _fleet.py."""

from __future__ import annotations

import fcntl
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import anyio.abc
import psutil

from autoskillit.core import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.fleet import DispatchRecord, DispatchStatus


def _transition_dead_dispatch(
    state_path: Path, record: DispatchRecord, reason: str
) -> DispatchStatus | None:
    """Transition a confirmed-dead RUNNING dispatch to RESUMABLE or INTERRUPTED.

    Delegates sidecar decision logic to the fleet layer. Returns the new status,
    or None if both write attempts failed. Never raises.
    """
    from autoskillit.fleet import crash_recover_dispatch  # noqa: PLC0415

    return crash_recover_dispatch(state_path, record, reason=reason)


@asynccontextmanager
async def _fleet_signal_guard(
    state_path: Path,
    campaign_id: str,
    *,
    campaign_name: str | None = None,
    cleanup_on_interrupt: bool = False,
) -> AsyncIterator[None]:
    """Async context manager that installs SIGINT/SIGTERM handlers.

    On signal receipt:
    - Cancels the enclosing task group scope first.
    - Reads state.json and marks RUNNING dispatches as INTERRUPTED.
    - Verifies PID identity via starttime_ticks before killing.
    - Optionally runs workspace cleanup.
    - Logs a resume hint.
    """

    async def _watch(
        scope: anyio.CancelScope,
        *,
        task_status: anyio.abc.TaskStatus = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT, signal.SIGHUP) as signals:
            task_status.started()
            async for sig in signals:
                signame = sig.name

                # Cancel the enclosing scope FIRST to unwind any in-flight dispatch
                # coroutine before the cleanup writes state (prevents ordering races).
                scope.cancel()

                # Shield the cleanup from the now-cancelled scope so that async
                # operations (kill, state write) are not interrupted.
                with anyio.CancelScope(shield=True):
                    from autoskillit.execution import async_kill_process_tree, read_starttime_ticks
                    from autoskillit.fleet import DispatchStatus, read_state  # noqa: PLC0415

                    state = read_state(state_path)
                    if state is not None:
                        for dispatch in state.dispatches:
                            if dispatch.status != DispatchStatus.RUNNING:
                                continue
                            if dispatch.l2_pid == 0:
                                _transition_dead_dispatch(
                                    state_path, dispatch, reason=f"signal_{signame}"
                                )
                                continue

                            # Verify PID identity before killing
                            current_ticks = read_starttime_ticks(dispatch.l2_pid)
                            if current_ticks is not None:
                                if (
                                    dispatch.l2_starttime_ticks > 0
                                    and current_ticks == dispatch.l2_starttime_ticks
                                ):
                                    try:
                                        await async_kill_process_tree(dispatch.l2_pid, timeout=5.0)
                                    except Exception:
                                        logger.warning(
                                            "signal_guard: kill_process_tree failed",
                                            exc_info=True,
                                        )
                                else:
                                    logger.warning(
                                        "signal_guard: PID %d recycled (ticks mismatch)",
                                        dispatch.l2_pid,
                                    )
                            else:
                                # Non-Linux fallback: psutil.pid_exists without identity check
                                if psutil.pid_exists(dispatch.l2_pid):
                                    try:
                                        await async_kill_process_tree(dispatch.l2_pid, timeout=5.0)
                                    except Exception:
                                        logger.warning(
                                            "signal_guard: kill_process_tree failed (non-linux)",
                                            exc_info=True,
                                        )

                            _transition_dead_dispatch(
                                state_path, dispatch, reason=f"signal_{signame}"
                            )

                    if cleanup_on_interrupt:
                        try:
                            from autoskillit.core import ensure_project_temp
                            from autoskillit.workspace import DefaultWorkspaceManager

                            workspace_dir = ensure_project_temp(Path.cwd())
                            mgr = DefaultWorkspaceManager()
                            mgr.delete_contents(workspace_dir)
                        except Exception:
                            logger.warning("signal_guard: workspace cleanup failed", exc_info=True)

                    if campaign_name is not None:
                        resume_cmd = (
                            f"autoskillit fleet campaign {campaign_name} --resume {campaign_id}"
                        )
                    else:
                        resume_cmd = f"autoskillit fleet campaign <name> --resume {campaign_id}"
                    sys.stderr.write(f"Campaign {campaign_id} interrupted. Resume: {resume_cmd}\n")
                return

    async with anyio.create_task_group() as tg:
        await tg.start(_watch, tg.cancel_scope)
        try:
            yield
        finally:
            tg.cancel_scope.cancel()


def _reap_stale_dispatches(state_path: Path, *, dry_run: bool = False) -> None:
    """Reap stale RUNNING dispatches with PID-recycling-safe identity checks.

    Uses fcntl.flock() to protect against concurrent reap invocations.
    For each RUNNING dispatch:
    - Boot-ID mismatch → reaped_pid_recycled (no kill)
    - Process dead → reaped_dead_pid
    - Process alive + ticks match → kill + reaped_orphan
    - Process alive + ticks mismatch → reaped_pid_recycled (no kill)
    """
    from autoskillit.execution import kill_process_tree, read_boot_id, read_starttime_ticks
    from autoskillit.fleet import DispatchStatus, read_state  # noqa: PLC0415

    current_boot_id = read_boot_id()

    if not state_path.exists():
        logger.info("reap: state file not found, nothing to reap: %s", state_path)
        return

    with open(state_path, "r+") as _lock_fh:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX)
        try:
            state = read_state(state_path)
            if state is None:
                logger.warning("reap: cannot read state file: %s", state_path)
                return

            running = [d for d in state.dispatches if d.status == DispatchStatus.RUNNING]
            if not running:
                logger.info("reap: no running dispatches in campaign %s", state.campaign_id)
                return

            logger.info(
                "reap: scanning %d dispatches in campaign %s", len(running), state.campaign_id
            )

            for dispatch in running:
                name = dispatch.name
                pid = dispatch.l2_pid

                if pid == 0:
                    if dry_run:
                        logger.info("reap: [WOULD MARK]  %s  pid=0  (no PID recorded)", name)
                    else:
                        new_status = _transition_dead_dispatch(
                            state_path, dispatch, reason="reaped_dead_pid"
                        )
                        if new_status is not None:
                            logger.info("reap: [MARKED]      %s  (no PID recorded)", name)
                        else:
                            logger.warning("reap: [MARK_FAILED] %s  (no PID recorded)", name)
                    continue

                # Boot ID check: if machine rebooted, all PIDs are recycled
                if (
                    dispatch.l2_boot_id != ""
                    and current_boot_id is not None
                    and dispatch.l2_boot_id != current_boot_id
                ):
                    if dry_run:
                        logger.info(
                            "reap: [WOULD MARK]  %s  pid=%d  (rebooted, pid_recycled)", name, pid
                        )
                    else:
                        _transition_dead_dispatch(
                            state_path, dispatch, reason="reaped_pid_recycled"
                        )
                        logger.info(
                            "reap: [MARKED]      %s  pid=%d  (rebooted, pid_recycled)",
                            name,
                            pid,
                        )
                    continue

                if not psutil.pid_exists(pid):
                    if dry_run:
                        logger.info("reap: [WOULD MARK]  %s  pid=%d  (process dead)", name, pid)
                    else:
                        _transition_dead_dispatch(state_path, dispatch, reason="reaped_dead_pid")
                        logger.info("reap: [MARKED]      %s  pid=%d  (process dead)", name, pid)
                    continue

                # Process is alive — check identity
                current_ticks = read_starttime_ticks(pid)
                if current_ticks is not None and current_ticks == dispatch.l2_starttime_ticks:
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
                        _transition_dead_dispatch(state_path, dispatch, reason="reaped_orphan")
                        logger.info("reap: [KILLED]      %s  pid=%d  (orphan reaped)", name, pid)
                else:
                    if dry_run:
                        logger.info(
                            "reap: [WOULD MARK]  %s  pid=%d  (PID recycled, no kill)", name, pid
                        )
                    else:
                        _transition_dead_dispatch(
                            state_path, dispatch, reason="reaped_pid_recycled"
                        )
                        logger.info(
                            "reap: [MARKED]      %s  pid=%d  (PID recycled, no kill)",
                            name,
                            pid,
                        )
        finally:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)


def _pick_resume_campaign(project_dir: Path) -> tuple[str, str]:
    """Interactively pick a resumable campaign. Returns (campaign_name, campaign_id) or exits."""
    from autoskillit.cli._menu import run_selection_menu  # noqa: PLC0415
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
