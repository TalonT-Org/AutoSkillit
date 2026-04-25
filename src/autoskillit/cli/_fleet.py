"""Fleet CLI sub-app: campaign management commands."""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import shutil
import signal
import sys
import time
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

from autoskillit.core import TerminalColumn, get_logger, is_feature_enabled
from autoskillit.fleet import (
    DispatchStatus,
    mark_dispatch_interrupted,
    read_state,
)

_log = get_logger(__name__)


def _require_fleet(cfg: AutomationConfig) -> None:
    """Exit with clear message if fleet feature is not enabled."""
    if not is_feature_enabled("fleet", cfg.features):
        print(
            "The 'fleet' feature is not enabled.\n"
            "Enable it with: features.fleet: true in your config\n"
            "Or set: AUTOSKILLIT_FEATURES__FLEET=true",
            file=sys.stderr,
        )
        raise SystemExit(1)


if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.fleet import CampaignState, DispatchRecord, ResumeDecision
    from autoskillit.recipe.schema import Recipe

_FAILURE_STATUSES = frozenset(
    {
        DispatchStatus.FAILURE,
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
        DispatchStatus.RELEASED,
    }
)

_IN_PROGRESS_STATUSES = frozenset(
    {
        DispatchStatus.RUNNING,
        DispatchStatus.PENDING,
    }
)

_STATUS_COLUMNS = (
    TerminalColumn("NAME", 30, "<"),
    TerminalColumn("STATUS", 12, "<"),
    TerminalColumn("ELAPSED", 10, ">"),
    TerminalColumn("INPUT", 10, ">"),
    TerminalColumn("OUTPUT", 10, ">"),
    TerminalColumn("CACHE_RD", 10, ">"),
    TerminalColumn("CACHE_WR", 10, ">"),
    TerminalColumn("SESSION_LOG", None, "<"),
)


def _compute_exit_code(state: CampaignState) -> int:
    """Compute CLI exit code from dispatch statuses.

    0 = all success/skipped, 1 = any failure, 2 = any in-progress.
    """
    has_failure = any(d.status in _FAILURE_STATUSES for d in state.dispatches)
    has_in_progress = any(d.status in _IN_PROGRESS_STATUSES for d in state.dispatches)
    if has_failure:
        return 1
    if has_in_progress:
        return 2
    return 0


def _fmt_elapsed(dispatch: DispatchRecord) -> str:
    """Format dispatch elapsed time as human-readable string."""
    if dispatch.started_at <= 0:
        return "-"
    if dispatch.status == DispatchStatus.RUNNING:
        seconds = time.time() - dispatch.started_at
    elif dispatch.ended_at > 0:
        seconds = dispatch.ended_at - dispatch.started_at
    else:
        return "-"
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _humanize(n: int | float | None) -> str:
    """Humanize token counts: 1234 -> '1.2k', 1234567 -> '1.2M'."""
    if n is None or n == 0:
        return "0"
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def _build_status_rows(state: CampaignState) -> list[tuple[str, ...]]:
    """Build table rows from campaign state dispatches, including separator and TOTAL rows."""
    rows: list[tuple[str, ...]] = []
    for d in state.dispatches:
        tu = d.token_usage
        rows.append(
            (
                d.name,
                str(d.status),
                _fmt_elapsed(d),
                _humanize(tu.get("input_tokens", 0)),
                _humanize(tu.get("output_tokens", 0)),
                _humanize(tu.get("cache_read_input_tokens", 0)),
                _humanize(tu.get("cache_creation_input_tokens", 0)),
                d.l2_session_log_dir or "-",
            )
        )
    totals = _aggregate_totals(state)
    rows.append(("─" * 6, "", "", "", "", "", "", ""))
    rows.append(
        (
            "TOTAL",
            "",
            "",
            _humanize(totals["input_tokens"]),
            _humanize(totals["output_tokens"]),
            _humanize(totals["cache_read"]),
            _humanize(totals["cache_creation"]),
            "",
        )
    )
    return rows


def _aggregate_totals(state: CampaignState) -> dict[str, int]:
    """Sum token_usage across all dispatches."""
    totals: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_creation": 0,
    }
    for d in state.dispatches:
        tu = d.token_usage
        totals["input_tokens"] += tu.get("input_tokens", 0)
        totals["output_tokens"] += tu.get("output_tokens", 0)
        totals["cache_read"] += tu.get("cache_read_input_tokens", 0)
        totals["cache_creation"] += tu.get("cache_creation_input_tokens", 0)
    return totals


def _load_log_totals(state: CampaignState) -> dict[str, int] | None:
    """Load token totals from sessions.jsonl for the campaign. Returns None if unavailable."""
    from autoskillit.execution import resolve_log_dir
    from autoskillit.pipeline import DefaultTokenLog

    log_root = resolve_log_dir("")
    sessions_index = log_root / "sessions.jsonl"
    if not sessions_index.exists():
        return None

    token_log = DefaultTokenLog()
    loaded = token_log.load_from_log_dir(log_root, campaign_id_filter=state.campaign_id)
    if loaded == 0:
        return None

    return token_log.compute_total()


def _cross_check_tokens(state: CampaignState, state_totals: dict[str, int]) -> None:
    """Warn on >5% token divergence between state.json and sessions.jsonl."""
    log_totals = _load_log_totals(state)
    if log_totals is None:
        return

    for label, state_key, log_key in [
        ("input_tokens", "input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens", "output_tokens"),
        ("cache_read", "cache_read", "cache_read_input_tokens"),
        ("cache_creation", "cache_creation", "cache_creation_input_tokens"),
    ]:
        sv = state_totals.get(state_key, 0)
        lv = log_totals.get(log_key, 0)
        if sv > 0 and abs(sv - lv) / sv > 0.05:
            sys.stderr.write(
                f"WARNING: {label} diverge {abs(sv - lv) / sv:.1%} (>5%)"
                f" (state={sv}, sessionlog={lv}); state.json wins\n"
            )


def _render_status_display(state: CampaignState) -> int:
    """Print campaign header and 8-column dispatch table to stdout.

    Returns the number of lines printed (for cursor-based screen refresh).
    """
    from autoskillit.cli._ansi import _render_terminal_table

    started = datetime.fromtimestamp(state.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = f"Campaign: {state.campaign_name}  ID: {state.campaign_id}  Started: {started}"
    print(header)

    rows = _build_status_rows(state)
    table_str = _render_terminal_table(_STATUS_COLUMNS, rows)
    print(table_str)
    return 1 + table_str.rstrip("\n").count("\n") + 1


def _watch_loop(state_path: Path) -> int:
    """1 Hz polling loop for fleet status. Returns exit code."""
    import select
    import termios
    import tty

    state = read_state(state_path)
    if state is None:
        sys.stderr.write("ERROR: state file disappeared or corrupted\n")
        return 3

    # If campaign already terminal, render once and exit without needing a TTY
    if all(d.status not in _IN_PROGRESS_STATUSES for d in state.dispatches):
        _render_status_display(state)
        print("\nAll dispatches complete.")
        return _compute_exit_code(state)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write(
            "ERROR: --watch requires an interactive terminal"
            " (both stdin and stdout must be TTYs).\n"
        )
        return 1

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    prev_lines = 0
    try:
        tty.setcbreak(fd)
        while True:
            state = read_state(state_path)
            if state is None:
                sys.stdout.flush()
                sys.stderr.write("ERROR: state file disappeared or corrupted\n")
                return 3

            if prev_lines > 0:
                sys.stdout.write(f"\033[{prev_lines}A")
                for _ in range(prev_lines):
                    sys.stdout.write("\033[2K\033[1B")
                sys.stdout.write(f"\033[{prev_lines}A")
            sys.stdout.flush()

            prev_lines = _render_status_display(state)

            if all(d.status not in _IN_PROGRESS_STATUSES for d in state.dispatches):
                print("\nAll dispatches complete.")
                return _compute_exit_code(state)

            rlist, _, _ = select.select([sys.stdin], [], [], 1.0)
            if rlist:
                ch = sys.stdin.read(1)
                if ch.lower() == "q":
                    return _compute_exit_code(state)
    except KeyboardInterrupt:
        state = read_state(state_path)
        return _compute_exit_code(state) if state else 3
    except Exception as exc:
        _log.error("unexpected error in --watch loop: %s", exc, exc_info=True)
        sys.stderr.write(f"ERROR: unexpected error in --watch loop: {exc}\n")
        return 3
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (termios.error, OSError):
            pass


fleet_app = App(name="fleet", help="Campaign fleet management.")


def _remove_clone_fn(path: str, _flag: str) -> dict[str, str]:
    """Remove a clone directory for batch_delete."""
    try:
        shutil.rmtree(path)
        return {"removed": "true"}
    except Exception as exc:
        _log.warning("Failed to remove clone %s: %s", path, exc, exc_info=True)
        return {"removed": "false", "reason": str(exc)}


def _launch_fleet_session(
    campaign_recipe: Recipe | None,
    campaign_id: str | None,
    state_path: Path | None,
    resume_metadata: ResumeDecision | None,
) -> None:
    """Build the L3 orchestrator prompt and launch an interactive fleet session."""
    from autoskillit.cli import detect_autoskillit_mcp_prefix  # noqa: PLC0415
    from autoskillit.cli._session_launch import _run_interactive_session

    mcp_prefix = detect_autoskillit_mcp_prefix()

    from autoskillit.core import NoResume

    project_dir = Path.cwd()

    if campaign_recipe is None:
        # Ad-hoc mode: no campaign, no state, bare kitchen open
        from autoskillit.cli._prompts import _build_fleet_open_prompt

        prompt = _build_fleet_open_prompt(mcp_prefix)
        extra_env: dict[str, str] = {
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
            "AUTOSKILLIT_HEADLESS": "0",
        }
        while True:
            reload_id = _run_interactive_session(
                prompt, extra_env=extra_env, resume_spec=NoResume(), project_dir=project_dir
            )
            if reload_id is None:
                break
    else:
        # Campaign-driven mode: full orchestrator prompt with manifest and state
        if campaign_id is None:
            raise ValueError("campaign_id must not be None in campaign-driven mode")
        if state_path is None:
            raise ValueError("state_path must not be None in campaign-driven mode")
        from autoskillit.cli._prompts import _build_l3_orchestrator_prompt
        from autoskillit.core import dump_yaml_str

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
        extra_env = {
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
            "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
            "AUTOSKILLIT_CAMPAIGN_STATE_PATH": str(state_path),
            "AUTOSKILLIT_HEADLESS": "0",
        }
        while True:
            reload_id = _run_interactive_session(
                prompt, extra_env=extra_env, resume_spec=NoResume(), project_dir=project_dir
            )
            if reload_id is None:
                break


@asynccontextmanager
async def _fleet_signal_guard(
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
                        f" Resume: autoskillit fleet run --resume {campaign_id}\n"
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


@fleet_app.command(name="run")
def fleet_run(
    campaign_name: str | None = None,
    *,
    resume_campaign: str | None = None,
) -> None:
    """Launch an interactive Claude Code session to execute a campaign."""
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'fleet run' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") == "leaf":
        print("ERROR: 'fleet run' cannot run inside a leaf session.")
        sys.exit(1)
    if shutil.which("claude") is None:
        print("ERROR: 'claude' not found. Install: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)

    # Ad-hoc mode: no campaign name supplied — launch bare fleet dispatcher
    if campaign_name is None:
        _launch_fleet_session(None, None, None, None)
        return

    from autoskillit.core import YAMLError
    from autoskillit.fleet import (
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
    fleet_dir = Path.cwd() / ".autoskillit" / "temp" / "fleet"

    if resume_campaign is not None:
        campaign_id = resume_campaign
        state_path = fleet_dir / campaign_id / "state.json"
        resume_metadata = resume_campaign_from_state(state_path, parsed.continue_on_failure)
        if resume_metadata is None:
            print(f"ERROR: Campaign state not found or corrupted for '{campaign_id}'")
            sys.exit(1)
    else:
        campaign_id = uuid4().hex[:16]
        state_dir = fleet_dir / campaign_id
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "state.json"
        dispatches = [DispatchRecord(name=d.name) for d in parsed.dispatches]
        write_initial_state(state_path, campaign_id, campaign_name, str(match.path), dispatches)

    _launch_fleet_session(parsed, campaign_id, state_path, resume_metadata)


@fleet_app.command(name="list")
def fleet_list() -> None:
    """List available campaign recipes."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)

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


@fleet_app.command(name="status")
def fleet_status(
    campaign_id: str | None = None,
    *,
    cleanup: bool = False,
    reap: bool = False,
    dry_run: bool = False,
    watch: bool = False,
    json_output: Annotated[bool, Parameter(name=["--json"])] = False,
) -> None:
    """Show fleet campaign status."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)

    fleet_dir = Path.cwd() / ".autoskillit" / "temp" / "fleet"

    if campaign_id is not None:
        state_path = fleet_dir / campaign_id / "state.json"
        state = read_state(state_path)
        if state is None:
            print(f"ERROR: Campaign '{campaign_id}' not found or state corrupted.")
            sys.exit(3)

        if json_output:
            totals = _aggregate_totals(state)
            data = {
                "campaign_id": state.campaign_id,
                "campaign_name": state.campaign_name,
                "started_at": state.started_at,
                "dispatches": [d.to_dict() for d in state.dispatches],
                "totals": totals,
            }
            print(json.dumps(data))
            _cross_check_tokens(state, totals)
            sys.exit(_compute_exit_code(state))

        if watch and cleanup:
            print("ERROR: --watch and --cleanup are mutually exclusive.")
            sys.exit(3)

        if watch:
            sys.exit(_watch_loop(state_path))

        _render_status_display(state)

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
                for d in state.dispatches:
                    if d.l2_session_id:
                        skill_mgr.cleanup_session(d.l2_session_id)
                skill_mgr.cleanup_session(campaign_id)
            except Exception:
                _log.warning(
                    "Session skill cleanup failed for campaign %s", campaign_id, exc_info=True
                )
            sweep_stale_markers()
            kitchen_state_dir = (
                Path.cwd() / ".autoskillit" / "temp" / "kitchen_state" / campaign_id
            )
            if kitchen_state_dir.is_dir():
                shutil.rmtree(kitchen_state_dir, ignore_errors=True)
            print(f"Cleanup complete for campaign '{campaign_id}'.")

        if reap or dry_run:
            _reap_stale_dispatches(state_path, dry_run=dry_run)

        totals = _aggregate_totals(state)
        _cross_check_tokens(state, totals)
        sys.exit(_compute_exit_code(state))

    else:
        from autoskillit.core import _render_terminal_table

        if not fleet_dir.exists():
            print("No campaigns found.")
            return

        subdirs = [d for d in fleet_dir.iterdir() if d.is_dir()]
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
        rows_list = []
        for subdir in sorted(subdirs):
            s = read_state(subdir / "state.json")
            if s is None:
                continue
            started = datetime.fromtimestamp(s.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            rows_list.append((s.campaign_name, s.campaign_id, str(len(s.dispatches)), started))

        if not rows_list:
            print("No campaigns found.")
            return

        print(_render_terminal_table(columns, rows_list))


def render_fleet_error(envelope_json: str) -> int:
    """Render a fleet error envelope to stderr.

    Returns exit code: 3 for fleet envelope errors, 0 for non-error envelopes.
    """
    try:
        data = json.loads(envelope_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    if data.get("success") is not False:
        return 0
    msg = data.get("user_visible_message") or "unknown error"
    code = data.get("error", "")
    sys.stderr.write(f"fleet error [{code}]: {msg}\n")
    details = data.get("details")
    if details:
        sys.stderr.write(f"  details: {json.dumps(details)}\n")
    return 3
