from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from cyclopts import App

sessions_app = App(name="sessions", help="Session diagnostics and analysis.")


def _refresh_report_index(log_root: Path, index_dir: Path, *, rebuild: bool) -> None:
    from autoskillit.core import ArtifactLeaseContention
    from autoskillit.execution import rebuild_report_index, update_report_index

    try:
        result = (
            rebuild_report_index(log_root, index_dir)
            if rebuild
            else update_report_index(log_root, index_dir)
        )
    except ArtifactLeaseContention as exc:
        print(
            f"report index: another operation holds a required index or source lease: {exc.path}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(f"report index: walked {result.items_walked} items, wrote {result.rows_written} rows")
    for source in result.source_gaps:
        print(
            f"report index: {source} cursor no longer retained; re-walked retained {source} data",
            file=sys.stderr,
        )


@sessions_app.command(name="index")
def sessions_index(*, update: bool = False, rebuild: bool = False) -> None:
    """Report the derived report index; refresh with --update or re-derive with --rebuild."""
    from autoskillit.config import load_config
    from autoskillit.execution import (
        REPORT_INDEX_SCHEMA_VERSION,
        read_report_index,
        report_index_dir,
        resolve_log_dir,
    )

    cfg = load_config()
    log_root = resolve_log_dir(cfg.linux_tracing.log_dir)
    index_dir = report_index_dir(log_root)

    if rebuild or update:
        _refresh_report_index(log_root, index_dir, rebuild=rebuild)

    report = read_report_index(index_dir)
    print(
        f"report index v{REPORT_INDEX_SCHEMA_VERSION} at {index_dir}: "
        f"sessions={len(report.sessions)} requests={len(report.requests)} "
        f"tools={len(report.tools)} subagents={len(report.subagents)}"
    )


@sessions_app.command(name="deck")
def sessions_deck(output: str) -> None:
    """Render the observability deck from the report index into one self-contained HTML file."""
    from autoskillit.config import load_config
    from autoskillit.core import atomic_write
    from autoskillit.execution import (
        REPORT_INDEX_SCHEMA_VERSION,
        read_report_index,
        report_index_dir,
        resolve_log_dir,
    )
    from autoskillit.report import render_deck

    log_root = resolve_log_dir(load_config().linux_tracing.log_dir)
    index_dir = report_index_dir(log_root)
    _refresh_report_index(log_root, index_dir, rebuild=False)
    report = read_report_index(index_dir)
    html = render_deck(
        report.sessions.values(),
        generated_at=datetime.now(UTC),
        index_schema_version=REPORT_INDEX_SCHEMA_VERSION,
    )
    atomic_write(Path(output), html)
    print(f"deck: wrote {output} ({len(report.sessions)} session rows)")


@sessions_app.command(name="analyze")
def sessions_analyze(
    recipe: str = "",
    *,
    format: str = "table",
    top: int = 20,
    min_count: int = 1,
    output: str = "",
) -> None:
    """Analyze cross-session tool call sequence patterns.

    Reads all session summary.json files from the configured log directory
    and renders a Data Flow Graph of tool call transitions.
    """
    from autoskillit.config import load_config
    from autoskillit.core import (
        atomic_write,
        compute_analysis,
        parse_sessions_from_summary_dir,
        render_adjacency_table,
        render_dot,
        render_mermaid,
    )
    from autoskillit.execution import resolve_log_dir

    cfg = load_config()
    log_root = resolve_log_dir(cfg.linux_tracing.log_dir)
    sessions = list(parse_sessions_from_summary_dir(log_root))

    if not sessions:
        print("No sessions with tool call data found.", file=sys.stderr)
        raise SystemExit(1)

    if recipe:
        sessions = [s for s in sessions if s.recipe_name == recipe]
        if not sessions:
            print(f"No sessions found for recipe '{recipe}'.", file=sys.stderr)
            raise SystemExit(1)

    result = compute_analysis(sessions)
    dfg = result.global_dfg if not recipe else result.by_recipe.get(recipe, result.global_dfg)

    fmt = format.lower()
    if fmt == "mermaid":
        rendered = render_mermaid(dfg, min_count=min_count, top_n=top)
    elif fmt == "dot":
        rendered = render_dot(dfg, min_count=min_count, top_n=top)
    else:
        rendered = render_adjacency_table(dfg, top_n=top)

    if output:
        import pathlib

        atomic_write(pathlib.Path(output), rendered)
        print(f"Written to {output}")
    else:
        print(rendered)

    print(
        f"\n{result.session_count} sessions | {len(result.by_recipe)} recipe(s)",
        file=sys.stderr,
    )
