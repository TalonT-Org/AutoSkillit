"""CLI runner for explicit retained Codex attempt-view reconciliation."""

from __future__ import annotations

import json
import sys


def run_codex_attempts(
    *,
    discard_view: str | None = None,
    reason: str | None = None,
    output_json: bool = False,
) -> None:
    """List retained attempt views, or explicitly discard one eligible view."""
    from autoskillit.core import TerminalColumn, _render_terminal_table, default_log_dir
    from autoskillit.execution import CodexSessionStore

    if discard_view is None and reason is not None:
        raise ValueError("--reason requires --discard-view")
    if discard_view is not None and reason is None:
        raise ValueError("--discard-view requires --reason")

    store = CodexSessionStore(log_dir=default_log_dir())
    reconciled = (
        store.discard_attempt_view(discard_view, reason)
        if discard_view is not None and reason is not None
        else None
    )
    views = store.list_retained_attempt_views()
    if output_json:
        sys.stdout.write(
            json.dumps(
                {"views": list(views), "reconciled": reconciled},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return

    if reconciled is not None:
        sys.stdout.write(f"reconciled Codex attempt view {reconciled['view_id']}\n")
    if not views:
        sys.stdout.write("no retained Codex attempt views\n")
        return
    columns = (
        TerminalColumn("VIEW", 32, "<"),
        TerminalColumn("STATE", 12, "<"),
        TerminalColumn("ELIGIBLE", 8, "<"),
        TerminalColumn("DETAIL", None, "<"),
    )
    rows = [
        (
            str(row["view_id"]),
            str(row["state"] or "unknown"),
            str(row["eligible"]).lower(),
            str(row["detail"]),
        )
        for row in views
    ]
    sys.stdout.write(_render_terminal_table(columns, rows) + "\n")
