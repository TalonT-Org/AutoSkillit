"""Fleet status display helpers extracted from _fleet.py."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import TerminalColumn, TokenMeasure, get_logger
from autoskillit.pipeline import TelemetryFormatter

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

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

    0 = all success/skipped, 1 = any failure, 2 = unresolved or in-progress.
    """
    from autoskillit.fleet import DispatchStatus  # noqa: PLC0415

    _failure = frozenset(
        {
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
            DispatchStatus.REFUSED,
            DispatchStatus.RELEASED,
        }
    )
    _in_progress = frozenset(
        {DispatchStatus.RUNNING, DispatchStatus.PENDING, DispatchStatus.RESUMABLE}
    )
    has_failure = any(d.status in _failure for d in state.dispatches)
    has_in_progress = bool(state.opaque_dispatches) or any(
        d.status in _in_progress or d.status == DispatchStatus.UNKNOWN for d in state.dispatches
    )
    if has_failure:
        return 1
    if has_in_progress:
        return 2
    return 0


def _fmt_elapsed(dispatch: DispatchRecord) -> str:
    """Format dispatch elapsed time as human-readable string."""
    from autoskillit.fleet import DispatchStatus  # noqa: PLC0415

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


def _pair_totals(state: CampaignState) -> list[dict[str, object]]:
    """Aggregate dispatch measures only inside their source pair."""
    totals: dict[tuple[str, str], dict[str, object]] = {}
    fields = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    for d in state.dispatches:
        tu = d.token_usage
        if not tu:
            continue
        backend = tu.get("backend")
        provider_used = tu.get("provider_used")
        if not isinstance(backend, str) or not isinstance(provider_used, str):
            continue
        key = (backend, provider_used)
        row = totals.get(key)
        if row is None:
            totals[key] = {
                "backend": backend,
                "provider_used": provider_used,
                **{field: tu[field] for field in fields},
            }
            continue
        for field in fields:
            try:
                left = TokenMeasure.from_dict(row[field])
                right = TokenMeasure.from_dict(tu[field])
            except (TypeError, ValueError, KeyError):
                row[field] = TokenMeasure.unknown().to_dict()
                continue
            row[field] = TokenMeasure.combine_or_unknown(left, right).to_dict()
    return list(totals.values())


def _build_status_rows(state: CampaignState) -> list[tuple[str, ...]]:
    """Build table rows from campaign state dispatches, including separator and TOTAL rows."""
    rows: list[tuple[str, ...]] = []
    for d in state.dispatches:
        tu = d.token_usage or {}
        backend = tu.get("backend")
        provider_used = tu.get("provider_used")
        source = f"{backend}/{provider_used}" if backend and provider_used else ""
        rows.append(
            (
                f"{d.name} ({source})" if source else d.name,
                str(d.status),
                _fmt_elapsed(d),
                TelemetryFormatter._humanize(tu.get("input_tokens")),
                TelemetryFormatter._humanize(tu.get("output_tokens")),
                TelemetryFormatter._humanize(tu.get("cache_read_tokens")),
                TelemetryFormatter._humanize(tu.get("cache_write_tokens")),
                d.dispatched_session_log_dir or "-",
            )
        )
    for raw in state.opaque_dispatches:
        name = (
            str(raw.get("name", "(opaque dispatch)"))
            if isinstance(raw, Mapping)
            else "(opaque dispatch)"
        )
        status = str(raw.get("status", "unknown")) if isinstance(raw, Mapping) else "unknown"
        rows.append((name, status, "-", "-", "-", "-", "-", "-"))
    for total in _pair_totals(state):
        rows.append(
            (
                f"TOTAL ({total['backend']}/{total['provider_used']})",
                "",
                "",
                TelemetryFormatter._humanize(total["input_tokens"]),
                TelemetryFormatter._humanize(total["output_tokens"]),
                TelemetryFormatter._humanize(total["cache_read_tokens"]),
                TelemetryFormatter._humanize(total["cache_write_tokens"]),
                "",
            )
        )
    return rows


def _render_status_display(state: CampaignState) -> int:
    """Print campaign header and 8-column dispatch table to stdout.

    Returns the number of lines printed (for cursor-based screen refresh).
    """
    from autoskillit.cli.ui._ansi import _render_terminal_table

    started = datetime.fromtimestamp(state.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = f"Campaign: {state.campaign_name}  ID: {state.campaign_id}  Started: {started}"
    print(header)

    rows = _build_status_rows(state)
    table_str = _render_terminal_table(_STATUS_COLUMNS, rows)
    print(table_str)
    return 1 + table_str.rstrip("\n").count("\n") + 1


def _poll_watch_loop(state_path: Path, in_progress: frozenset[DispatchStatus]) -> int:
    """Render campaign states and process quit input until a terminal outcome."""
    import select

    from autoskillit.fleet import read_state

    prev_lines = 0
    while True:
        state = read_state(state_path)
        if state is None:
            sys.stdout.flush()
            sys.stderr.write("ERROR: state file disappeared or corrupted\n")
            return 3
        if state.opaque_dispatches:
            prev_lines = _render_status_display(state)
            print("\nCampaign contains unsupported dispatch state.")
            return 2

        if prev_lines > 0:
            sys.stdout.write(f"\033[{prev_lines}A")
            for _ in range(prev_lines):
                sys.stdout.write("\033[2K\033[1B")
            sys.stdout.write(f"\033[{prev_lines}A")
        sys.stdout.flush()

        prev_lines = _render_status_display(state)

        if all(d.status not in in_progress for d in state.dispatches):
            print("\nAll dispatches complete.")
            return _compute_exit_code(state)

        rlist, _, _ = select.select([sys.stdin], [], [], 1.0)
        if rlist:
            ch = sys.stdin.read(1)
            if ch.lower() == "q":
                return _compute_exit_code(state)


def _watch_loop(state_path: Path) -> int:
    """1 Hz polling loop for fleet status. Returns exit code."""
    import termios
    import tty

    from autoskillit.fleet import DispatchStatus, read_state  # noqa: PLC0415

    _in_progress = frozenset({DispatchStatus.RUNNING, DispatchStatus.PENDING})

    state = read_state(state_path)
    if state is None:
        sys.stderr.write("ERROR: state file disappeared or corrupted\n")
        return 3
    if state.opaque_dispatches:
        _render_status_display(state)
        print("\nCampaign contains unsupported dispatch state.")
        return 2

    # If campaign already terminal, render once and exit without needing a TTY
    if all(d.status not in _in_progress for d in state.dispatches):
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
    try:
        tty.setcbreak(fd)
        return _poll_watch_loop(state_path, _in_progress)
    except KeyboardInterrupt:
        state = read_state(state_path)
        return _compute_exit_code(state) if state else 3
    except Exception as exc:
        logger.error("unexpected error in --watch loop: %s", exc, exc_info=True)
        sys.stderr.write(f"ERROR: unexpected error in --watch loop: {exc}\n")
        return 3
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (termios.error, OSError):
            pass


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
