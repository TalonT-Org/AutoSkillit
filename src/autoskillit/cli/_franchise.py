"""Franchise CLI sub-app: campaign management commands."""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import shutil
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

import anyio
import anyio.abc
import psutil
from cyclopts import App, Parameter

from autoskillit.core import get_logger
from autoskillit.franchise import (
    DispatchStatus,
    mark_dispatch_interrupted,
    read_state,
)

_log = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.franchise import ResumeDecision
    from autoskillit.recipe.schema import Recipe

franchise_app = App(name="franchise", help="Campaign franchise management.")


def _remove_clone_fn(path: str, _flag: str) -> dict[str, str]:
    """Remove a clone directory for batch_delete."""
    try:
        shutil.rmtree(path)
        return {"removed": "true"}
    except Exception as exc:
        _log.warning("Failed to remove clone %s: %s", path, exc, exc_info=True)
        return {"removed": "false", "reason": str(exc)}


def _launch_franchise_session(
    campaign_recipe: Recipe,
    campaign_id: str,
    state_path: Path,
    resume_metadata: ResumeDecision | None,
) -> None:
    """Build the L3 orchestrator prompt and launch an interactive franchise session."""
    from autoskillit.cli._mcp_names import detect_autoskillit_mcp_prefix
    from autoskillit.cli._prompts import _build_l3_orchestrator_prompt
    from autoskillit.cli._session_launch import _run_interactive_session
    from autoskillit.core import dump_yaml_str

    mcp_prefix = detect_autoskillit_mcp_prefix()
    manifest_yaml = dump_yaml_str(
        [dataclasses.asdict(d) for d in campaign_recipe.dispatches],
        default_flow_style=False,
        allow_unicode=True,
    )
    completed_dispatches = (
        resume_metadata.completed_dispatches_block if resume_metadata is not None else ""
    )
    prompt = _build_l3_orchestrator_prompt(
        campaign_recipe, manifest_yaml, completed_dispatches, mcp_prefix, campaign_id
    )
    extra_env: dict[str, str] = {
        "AUTOSKILLIT_SESSION_TYPE": "franchise",
        "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH": str(state_path),
        "AUTOSKILLIT_HEADLESS": "0",
    }
    _run_interactive_session(prompt, extra_env=extra_env)


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
                                    _log.warning(
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
                                        _log.warning(
                                            "signal_guard: kill_process_tree failed",
                                            exc_info=True,
                                        )
                                else:
                                    _log.warning(
                                        "signal_guard: PID %d recycled (ticks mismatch)",
                                        dispatch.l2_pid,
                                    )
                            else:
                                # Non-Linux fallback: psutil.pid_exists without identity check
                                if psutil.pid_exists(dispatch.l2_pid):
                                    try:
                                        await async_kill_process_tree(dispatch.l2_pid, timeout=5.0)
                                    except Exception:
                                        _log.warning(
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
                                _log.warning(
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
                            _log.warning("signal_guard: workspace cleanup failed", exc_info=True)

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

    if not state_path.exists():
        _log.info("reap: state file not found, nothing to reap: %s", state_path)
        return

    with open(state_path, "r+") as _lock_fh:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX)
        try:
            state = read_state(state_path)
            if state is None:
                _log.warning("reap: cannot read state file: %s", state_path)
                return

            running = [d for d in state.dispatches if d.status == DispatchStatus.RUNNING]
            if not running:
                _log.info("reap: no running dispatches in campaign %s", state.campaign_id)
                return

            _log.info(
                "reap: scanning %d dispatches in campaign %s", len(running), state.campaign_id
            )

            for dispatch in running:
                name = dispatch.name
                pid = dispatch.l2_pid

                if pid == 0:
                    _action = "reaped_dead_pid"
                    if dry_run:
                        _log.info("reap: [WOULD MARK]  %s  pid=0  (no PID recorded)", name)
                    else:
                        try:
                            mark_dispatch_interrupted(state_path, name, reason=_action)
                            _log.info("reap: [MARKED]      %s  (no PID recorded)", name)
                        except ValueError:
                            _log.info("reap: [SKIPPED]     %s  (already terminal)", name)
                    continue

                # Boot ID check: if machine rebooted, all PIDs are recycled
                if (
                    dispatch.l2_boot_id != ""
                    and current_boot_id is not None
                    and dispatch.l2_boot_id != current_boot_id
                ):
                    if dry_run:
                        _log.info(
                            "reap: [WOULD MARK]  %s  pid=%d  (rebooted, pid_recycled)", name, pid
                        )
                    else:
                        try:
                            mark_dispatch_interrupted(
                                state_path, name, reason="reaped_pid_recycled"
                            )
                            _log.info(
                                "reap: [MARKED]      %s  pid=%d  (rebooted, pid_recycled)",
                                name,
                                pid,
                            )
                        except ValueError:
                            _log.info("reap: [SKIPPED]     %s  (already terminal)", name)
                    continue

                if not psutil.pid_exists(pid):
                    if dry_run:
                        _log.info("reap: [WOULD MARK]  %s  pid=%d  (process dead)", name, pid)
                    else:
                        try:
                            mark_dispatch_interrupted(state_path, name, reason="reaped_dead_pid")
                            _log.info("reap: [MARKED]      %s  pid=%d  (process dead)", name, pid)
                        except ValueError:
                            _log.info("reap: [SKIPPED]     %s  (already terminal)", name)
                    continue

                # Process is alive — check identity
                current_ticks = read_starttime_ticks(pid)
                if current_ticks is not None and current_ticks == dispatch.l2_starttime_ticks:
                    if dry_run:
                        _log.info(
                            "reap: [WOULD KILL]  %s  pid=%d  (orphan, identity match)", name, pid
                        )
                    else:
                        try:
                            kill_process_tree(pid)
                        except Exception:
                            _log.warning(
                                "reap: kill_process_tree failed for pid=%d", pid, exc_info=True
                            )
                        try:
                            mark_dispatch_interrupted(state_path, name, reason="reaped_orphan")
                            _log.info("reap: [KILLED]      %s  pid=%d  (orphan reaped)", name, pid)
                        except ValueError:
                            _log.info("reap: [SKIPPED]     %s  (already terminal)", name)
                else:
                    if dry_run:
                        _log.info(
                            "reap: [WOULD MARK]  %s  pid=%d  (PID recycled, no kill)", name, pid
                        )
                    else:
                        try:
                            mark_dispatch_interrupted(
                                state_path, name, reason="reaped_pid_recycled"
                            )
                            _log.info(
                                "reap: [MARKED]      %s  pid=%d  (PID recycled, no kill)",
                                name,
                                pid,
                            )
                        except ValueError:
                            _log.info("reap: [SKIPPED]     %s  (already terminal)", name)
        finally:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)


@franchise_app.command(name="run")
def franchise_run(
    campaign_name: str,
    *,
    resume_campaign: str | None = None,
) -> None:
    """Launch an interactive Claude Code session to execute a campaign."""
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'franchise run' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") == "leaf":
        print("ERROR: 'franchise run' cannot run inside a leaf session.")
        sys.exit(1)
    if shutil.which("claude") is None:
        print("ERROR: 'claude' not found. Install: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    from autoskillit.core import YAMLError
    from autoskillit.franchise import (
        DispatchRecord,
        resume_campaign_from_state,
        write_initial_state,
    )
    from autoskillit.recipe import find_campaign_by_name, load_recipe, validate_recipe

    match = find_campaign_by_name(campaign_name, Path.cwd())
    if match is None:
        print(f"Campaign not found: '{campaign_name}'")
        sys.exit(1)

    try:
        parsed = load_recipe(match.path)
    except YAMLError as exc:
        print(f"Campaign YAML parse error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Campaign structure error: {exc}")
        sys.exit(1)

    errors = validate_recipe(parsed)
    if errors:
        print(f"Campaign '{campaign_name}' failed validation:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    resume_metadata: ResumeDecision | None = None
    campaign_id: str
    state_path: Path
    franchise_dir = Path.cwd() / ".autoskillit" / "temp" / "franchise"

    if resume_campaign is not None:
        campaign_id = resume_campaign
        state_path = franchise_dir / campaign_id / "state.json"
        resume_metadata = resume_campaign_from_state(state_path, parsed.continue_on_failure)
        if resume_metadata is None:
            print(f"ERROR: Campaign state not found or corrupted for '{campaign_id}'")
            sys.exit(1)
    else:
        campaign_id = uuid4().hex[:16]
        state_dir = franchise_dir / campaign_id
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "state.json"
        dispatches = [DispatchRecord(name=d.name) for d in parsed.dispatches]
        write_initial_state(state_path, campaign_id, campaign_name, str(match.path), dispatches)

    _launch_franchise_session(parsed, campaign_id, state_path, resume_metadata)


@franchise_app.command(name="list")
def franchise_list() -> None:
    """List available campaign recipes."""
    from autoskillit.core import TerminalColumn, _render_terminal_table
    from autoskillit.recipe import list_campaign_recipes

    result = list_campaign_recipes(Path.cwd())
    if not result.items:
        print("No campaigns found.")
        return

    columns = [
        TerminalColumn("NAME", 30, "<"),
        TerminalColumn("SOURCE", 10, "<"),
        TerminalColumn("DIR", 40, "<"),
    ]
    rows = [(r.name, r.source.value, str(r.path.parent.name)) for r in result.items]
    print(_render_terminal_table(columns, rows))


@franchise_app.command(name="status")
def franchise_status(
    campaign_id: str | None = None,
    *,
    cleanup: bool = False,
    reap: bool = False,
    dry_run: bool = False,
    watch: bool = False,
    json_output: Annotated[bool, Parameter(name=["--json"])] = False,
) -> None:
    """Show franchise campaign status."""
    from autoskillit.core import TerminalColumn, _render_terminal_table

    franchise_dir = Path.cwd() / ".autoskillit" / "temp" / "franchise"

    if campaign_id is not None:
        state_path = franchise_dir / campaign_id / "state.json"
        state = read_state(state_path)
        if state is None:
            print(f"ERROR: Campaign '{campaign_id}' not found or state corrupted.")
            sys.exit(1)

        if json_output:
            data = {
                "campaign_id": state.campaign_id,
                "campaign_name": state.campaign_name,
                "started_at": state.started_at,
                "dispatches": [d.to_dict() for d in state.dispatches],
            }
            print(json.dumps(data))
            return

        started = datetime.fromtimestamp(state.started_at, tz=UTC).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        print(f"Campaign: {state.campaign_name}  ID: {state.campaign_id}  Started: {started}")
        columns = [
            TerminalColumn("NAME", 30, "<"),
            TerminalColumn("STATUS", 12, "<"),
            TerminalColumn("DISPATCH_ID", 36, "<"),
            TerminalColumn("REASON", 40, "<"),
        ]
        rows = [(d.name, str(d.status), d.dispatch_id, d.reason) for d in state.dispatches]
        print(_render_terminal_table(columns, rows))

        if cleanup:
            from autoskillit.core import sweep_stale_markers
            from autoskillit.workspace import (
                DefaultSessionSkillManager,
                SkillsDirectoryProvider,
                batch_delete,
                resolve_ephemeral_root,
            )

            batch_delete("", _remove_clone_fn, owner=campaign_id)
            try:
                skill_mgr = DefaultSessionSkillManager(
                    provider=SkillsDirectoryProvider(),
                    ephemeral_root=resolve_ephemeral_root(),
                )
                skill_mgr.cleanup_session(campaign_id)
            except Exception:
                _log.warning(
                    "Session skill cleanup failed for campaign %s", campaign_id, exc_info=True
                )
            sweep_stale_markers()
            print(f"Cleanup complete for campaign '{campaign_id}'.")

        if reap or dry_run:
            _reap_stale_dispatches(state_path, dry_run=dry_run)

        if watch:
            raise NotImplementedError("--watch is not yet implemented.")

    else:
        if not franchise_dir.exists():
            print("No campaigns found.")
            return

        subdirs = [d for d in franchise_dir.iterdir() if d.is_dir()]
        if not subdirs:
            print("No campaigns found.")
            return

        if json_output:
            summaries = []
            for subdir in sorted(subdirs):
                s = read_state(subdir / "state.json")
                if s is None:
                    continue
                status_counts: dict[str, int] = {}
                for d in s.dispatches:
                    status_counts[d.status] = status_counts.get(d.status, 0) + 1
                summaries.append(
                    {
                        "campaign_id": s.campaign_id,
                        "campaign_name": s.campaign_name,
                        "started_at": s.started_at,
                        "dispatch_count": len(s.dispatches),
                        "status_counts": status_counts,
                    }
                )
            print(json.dumps(summaries))
            return

        columns = [
            TerminalColumn("CAMPAIGN_NAME", 30, "<"),
            TerminalColumn("ID", 18, "<"),
            TerminalColumn("DISPATCHES", 10, "<"),
            TerminalColumn("STARTED", 24, "<"),
        ]
        rows = []
        for subdir in sorted(subdirs):
            s = read_state(subdir / "state.json")
            if s is None:
                continue
            started = datetime.fromtimestamp(s.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            rows.append((s.campaign_name, s.campaign_id, str(len(s.dispatches)), started))

        if not rows:
            print("No campaigns found.")
            return

        print(_render_terminal_table(columns, rows))


def render_franchise_error(envelope_json: str) -> int:
    """Render a franchise error envelope to stderr.

    Returns exit code: 3 for franchise envelope errors, 0 for non-error envelopes.
    """
    try:
        data = json.loads(envelope_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    if data.get("success") is not False:
        return 0
    msg = data.get("user_visible_message") or "unknown error"
    code = data.get("error", "")
    sys.stderr.write(f"franchise error [{code}]: {msg}\n")
    details = data.get("details")
    if details:
        sys.stderr.write(f"  details: {json.dumps(details)}\n")
    return 3
