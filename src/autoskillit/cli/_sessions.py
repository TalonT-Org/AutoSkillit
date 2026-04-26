from __future__ import annotations

import sys

from cyclopts import App

sessions_app = App(name="sessions", help="Session diagnostics and analysis.")


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
