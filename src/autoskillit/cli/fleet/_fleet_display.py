"""Fleet status display helpers extracted from _fleet.py."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import TerminalColumn, get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignState, DispatchRecord

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
    has_in_progress = any(d.status in _in_progress for d in state.dispatches)
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
                d.l3_session_log_dir or "-",
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
    from autoskillit.cli.ui._ansi import _render_terminal_table

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

    from autoskillit.fleet import DispatchStatus, read_state  # noqa: PLC0415

    _in_progress = frozenset({DispatchStatus.RUNNING, DispatchStatus.PENDING})

    state = read_state(state_path)
    if state is None:
        sys.stderr.write("ERROR: state file disappeared or corrupted\n")
        return 3

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

            if all(d.status not in _in_progress for d in state.dispatches):
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
