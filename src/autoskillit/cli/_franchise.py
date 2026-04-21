"""Franchise CLI subcommands: status (with --reap / --dry-run) and run (stub)."""

from __future__ import annotations

import fcntl
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import anyio.abc
import psutil
from cyclopts import App

from autoskillit.core import get_logger
from autoskillit.franchise import (
    DispatchStatus,
    mark_dispatch_interrupted,
    read_state,
)

logger = get_logger(__name__)

franchise_app = App(name="franchise", help="Franchise campaign management.")


@asynccontextmanager
async def _franchise_signal_guard(
    state_path: Path,
    campaign_id: str,
    *,
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
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
            task_status.started()
            async for sig in signals:
                signame = "SIGINT" if sig == signal.SIGINT else "SIGTERM"

                # Cancel the enclosing scope FIRST to unwind any in-flight dispatch
                # coroutine before the cleanup writes state (prevents ordering races).
                scope.cancel()

                # Shield the cleanup from the now-cancelled scope so that async
                # operations (kill, state write) are not interrupted.
                with anyio.CancelScope(shield=True):
                    from autoskillit.execution import async_kill_process_tree, read_starttime_ticks

                    state = read_state(state_path)
                    if state is not None:
                        for dispatch in state.dispatches:
                            if dispatch.status != DispatchStatus.RUNNING:
                                continue
                            if dispatch.l2_pid == 0:
                                try:
                                    mark_dispatch_interrupted(
                                        state_path,
                                        dispatch.name,
                                        reason=f"signal_{signame}",
                                    )
                                except Exception:
                                    logger.warning(
                                        "signal_guard: failed to mark dispatch interrupted",
                                        exc_info=True,
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

                            try:
                                mark_dispatch_interrupted(
                                    state_path,
                                    dispatch.name,
                                    reason=f"signal_{signame}",
                                )
                            except Exception:
                                logger.warning(
                                    "signal_guard: failed to mark dispatch interrupted",
                                    exc_info=True,
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

                    sys.stderr.write(
                        f"Campaign {campaign_id} interrupted."
                        f" Resume: autoskillit franchise run --resume {campaign_id}\n"
                    )
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

    current_boot_id = read_boot_id()

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
                    _action = "reaped_dead_pid"
                    if dry_run:
                        logger.info("reap: [WOULD MARK]  %s  pid=0  (no PID recorded)", name)
                    else:
                        try:
                            mark_dispatch_interrupted(state_path, name, reason=_action)
                            logger.info("reap: [MARKED]      %s  (no PID recorded)", name)
                        except ValueError:
                            logger.info("reap: [SKIPPED]     %s  (already terminal)", name)
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
                        try:
                            mark_dispatch_interrupted(
                                state_path, name, reason="reaped_pid_recycled"
                            )
                            logger.info(
                                "reap: [MARKED]      %s  pid=%d  (rebooted, pid_recycled)",
                                name,
                                pid,
                            )
                        except ValueError:
                            logger.info("reap: [SKIPPED]     %s  (already terminal)", name)
                    continue

                if not psutil.pid_exists(pid):
                    if dry_run:
                        logger.info("reap: [WOULD MARK]  %s  pid=%d  (process dead)", name, pid)
                    else:
                        try:
                            mark_dispatch_interrupted(state_path, name, reason="reaped_dead_pid")
                            logger.info(
                                "reap: [MARKED]      %s  pid=%d  (process dead)", name, pid
                            )
                        except ValueError:
                            logger.info("reap: [SKIPPED]     %s  (already terminal)", name)
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
                        try:
                            mark_dispatch_interrupted(state_path, name, reason="reaped_orphan")
                            logger.info(
                                "reap: [KILLED]      %s  pid=%d  (orphan reaped)", name, pid
                            )
                        except ValueError:
                            logger.info("reap: [SKIPPED]     %s  (already terminal)", name)
                else:
                    if dry_run:
                        logger.info(
                            "reap: [WOULD MARK]  %s  pid=%d  (PID recycled, no kill)", name, pid
                        )
                    else:
                        try:
                            mark_dispatch_interrupted(
                                state_path, name, reason="reaped_pid_recycled"
                            )
                            logger.info(
                                "reap: [MARKED]      %s  pid=%d  (PID recycled, no kill)",
                                name,
                                pid,
                            )
                        except ValueError:
                            logger.info("reap: [SKIPPED]     %s  (already terminal)", name)
        finally:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)


def _state_path_for_campaign(campaign_id: str) -> Path:
    """Resolve the state.json path for a campaign ID from the temp dir."""
    from autoskillit.core import ensure_project_temp

    return ensure_project_temp(Path.cwd()) / "dispatches" / f"{campaign_id}.json"


@franchise_app.command(name="status")
def franchise_status(
    campaign_id: str,
    *,
    reap: bool = False,
    dry_run: bool = False,
) -> None:
    """Show campaign status. Use --reap to clean up orphaned dispatches."""
    state_path = _state_path_for_campaign(campaign_id)

    if dry_run or reap:
        _reap_stale_dispatches(state_path, dry_run=dry_run)
        return

    state = read_state(state_path)
    if state is None:
        logger.warning("franchise: campaign %r not found or state file unreadable", campaign_id)
        return

    logger.info("franchise: campaign %s  (%s)", state.campaign_id, state.campaign_name)
    for d in state.dispatches:
        pid_info = f"  pid={d.l2_pid}" if d.l2_pid else ""
        reason_info = f"  reason={d.reason!r}" if d.reason else ""
        logger.info("franchise:   %s: %s%s%s", d.name, d.status, pid_info, reason_info)


@franchise_app.command(name="run")
def franchise_run(
    manifest: str,
    *,
    cleanup_on_interrupt: bool = False,
) -> None:
    """Run a franchise campaign from a manifest file."""
    anyio.run(_franchise_run_async, manifest, cleanup_on_interrupt)


async def _franchise_run_async(manifest: str, cleanup_on_interrupt: bool) -> None:
    """Async entry point for franchise run with signal guard wiring."""
    import uuid

    campaign_id = str(uuid.uuid4())
    state_path = _state_path_for_campaign(campaign_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    async with _franchise_signal_guard(
        state_path,
        campaign_id,
        cleanup_on_interrupt=cleanup_on_interrupt,
    ):
        pass
