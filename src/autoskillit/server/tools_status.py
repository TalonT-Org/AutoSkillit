"""MCP tool handlers: kitchen_status, get_pipeline_report, get_token_summary,
get_timing_summary, read_db."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import atomic_write, get_logger
from autoskillit.pipeline import TelemetryFormatter
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _notify,
    _require_enabled,
    resolve_log_dir,
    track_response_size,
    write_telemetry_clear_marker,
)

logger = get_logger(__name__)


def _get_log_root() -> Path:
    """Return the resolved log root directory for the current context."""
    from autoskillit.server import _get_ctx

    return resolve_log_dir(_get_ctx().config.linux_tracing.log_dir)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@track_response_size("kitchen_status")
async def kitchen_status() -> str:
    """Return version health and configuration status for the running server.

    Reports package version, plugin.json version, version match status,
    tools enabled state, and active configuration summary. Call this after
    enabling tools or anytime you need to verify the server is healthy.

    This tool requires the kitchen to be open (gated by open_kitchen).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="kitchen_status"):
            from autoskillit.server import _get_config, _get_ctx, version_info

            info = version_info()
            status = {
                "package_version": info["package_version"],
                "plugin_json_version": info["plugin_json_version"],
                "versions_match": info["match"],
                "tools_enabled": _get_ctx().gate.enabled,
            }
            if not info["match"]:
                status["warning"] = (
                    f"Version mismatch: package is {info['package_version']} but "
                    f"plugin.json reports {info['plugin_json_version']}. "
                    f"Run `autoskillit doctor` for details or "
                    f"`autoskillit install` to refresh the plugin cache."
                )
            status["token_usage_verbosity"] = _get_config().token_usage.verbosity
            status["quota_guard_enabled"] = _get_config().quota_guard.enabled
            github_client = _get_ctx().github_client
            status["github_token_configured"] = (
                github_client.has_token if github_client is not None else False
            )
            status["github_default_repo"] = _get_config().github.default_repo
            return json.dumps(status)
    except Exception as exc:
        logger.error("kitchen_status unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("get_pipeline_report")
async def get_pipeline_report(clear: bool = False) -> str:
    """Return accumulated run_skill failures since last clear.

    Orchestrators should call this at the end of a pipeline run to retrieve
    a structured summary of every non-success result. Pass clear=True to
    atomically retrieve and reset the store for the next pipeline run.

    Returns JSON with:
      - total_failures: int
      - failures: list of {timestamp, skill_command, exit_code, subtype,
                            needs_retry, retry_reason, stderr}

    This tool requires the kitchen to be open (gated by open_kitchen).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="get_pipeline_report"):
            from autoskillit.server._state import _startup_ready

            if _startup_ready is not None and not _startup_ready.is_set():
                try:
                    await asyncio.wait_for(_startup_ready.wait(), timeout=30.0)
                except TimeoutError:
                    logger.warning("startup_ready_timeout", timeout=30.0)
                    return json.dumps(
                        {
                            "total_failures": 0,
                            "failures": [],
                            "warning": "Startup initialization did not complete within 30s",
                        }
                    )

            from autoskillit.server import _get_ctx

            failures = _get_ctx().audit.get_report_as_dicts()
            if clear:
                _get_ctx().audit.clear()
                try:
                    write_telemetry_clear_marker(_get_log_root())
                except Exception:
                    logger.debug("write_telemetry_clear_marker failed", exc_info=True)
            return json.dumps(
                {
                    "total_failures": len(failures),
                    "failures": failures,
                }
            )
    except Exception as exc:
        logger.error("get_pipeline_report unhandled exception", exc_info=True)
        return json.dumps(
            {"total_failures": 0, "failures": [], "error": f"{type(exc).__name__}: {exc}"}
        )


def _merge_wall_clock_seconds(steps: list[dict], timing_report: list[dict]) -> list[dict]:
    """Add wall_clock_seconds to each token step from timing log; fall back to elapsed_seconds."""
    timing_by_step = {e["step_name"]: e["total_seconds"] for e in timing_report}
    for step in steps:
        sn = step.get("step_name", "")
        step["wall_clock_seconds"] = timing_by_step.get(sn, step.get("elapsed_seconds", 0.0))
    return steps


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "telemetry", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("get_token_summary")
async def get_token_summary(clear: bool = False, format: str = "json", order_id: str = "") -> str:
    """Return accumulated run_skill token usage grouped by step name.

    Returns JSON with:
    - steps: list of {step_name, input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       invocation_count, wall_clock_seconds}
    - total: {input_tokens, output_tokens, cache_creation_input_tokens,
               cache_read_input_tokens}

    This tool sends no MCP progress notifications.

    Args:
        clear: If True, reset the token log after returning current data.
        format: Output format — "json" (default) returns structured JSON,
                "table" returns a pre-formatted markdown table string.
        order_id: If non-empty, return only token entries for this specific order/issue.
                  Empty string (default) returns aggregated data for all orders.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="get_token_summary"):
            from autoskillit.server import _get_ctx

            ctx = _get_ctx()
            steps = _merge_wall_clock_seconds(
                ctx.token_log.get_report(order_id=order_id),
                ctx.timing_log.get_report(order_id=order_id),
            )
            total = ctx.token_log.compute_total(order_id=order_id)
            mcp_report = ctx.response_log.get_report()
            mcp_total = ctx.response_log.compute_total()
            if clear:
                ctx.token_log.clear()
                ctx.response_log.clear()
                try:
                    write_telemetry_clear_marker(_get_log_root())
                except Exception:
                    logger.debug("write_telemetry_clear_marker failed", exc_info=True)
            if format == "table":
                return TelemetryFormatter.format_token_table(steps, total)
            return json.dumps(
                {
                    "steps": steps,
                    "total": total,
                    "mcp_responses": {
                        "steps": mcp_report,
                        "total": mcp_total,
                    },
                }
            )
    except Exception as exc:
        logger.error("get_token_summary unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "telemetry", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("get_timing_summary")
async def get_timing_summary(clear: bool = False, format: str = "json", order_id: str = "") -> str:
    """Return accumulated wall-clock timing grouped by step name.

    Returns JSON with:
    - steps: list of {step_name, total_seconds, invocation_count}
    - total: {total_seconds}

    Args:
        clear: If True, reset the timing log after returning current data.
        format: Output format — "json" (default) returns structured JSON,
                "table" returns a pre-formatted markdown table string.
        order_id: If non-empty, return only timing entries for this specific order/issue.
                  Empty string (default) returns aggregated data for all orders.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="get_timing_summary"):
            from autoskillit.server import _get_ctx

            steps = _get_ctx().timing_log.get_report(order_id=order_id)
            total = _get_ctx().timing_log.compute_total(order_id=order_id)
            if clear:
                _get_ctx().timing_log.clear()
                try:
                    write_telemetry_clear_marker(_get_log_root())
                except Exception:
                    logger.debug("write_telemetry_clear_marker failed", exc_info=True)
            if format == "table":
                return TelemetryFormatter.format_timing_table(steps, total)
            return json.dumps({"steps": steps, "total": total})
    except Exception as exc:
        logger.error("get_timing_summary unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "telemetry"},
    annotations={"readOnlyHint": True},
)
@track_response_size("analyze_tool_sequences")
async def analyze_tool_sequences(
    recipe: str = "",
    format: str = "table",
    top_n: int = 20,
    min_count: int = 1,
) -> str:
    """Analyze cross-session tool call sequences and return a DFG summary.

    Args:
        recipe: Filter to a specific recipe name (empty = all recipes).
        format: Output format — "table", "mermaid", or "dot".
        top_n: Limit rendering to top-N bigrams by frequency.
        min_count: Exclude bigrams with count below this threshold.

    Returns JSON with fields: session_count, recipe_count, top_bigrams,
    rendering (the formatted DFG string).
    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="analyze_tool_sequences"):
            _valid_formats = {"table", "mermaid", "dot"}
            if format not in _valid_formats:
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"Invalid format '{format}'; must be one of {sorted(_valid_formats)}"
                        ),
                    }
                )
            if top_n < 1:
                return json.dumps({"success": False, "error": f"top_n must be >= 1, got {top_n}"})
            if min_count < 1:
                return json.dumps(
                    {"success": False, "error": f"min_count must be >= 1, got {min_count}"}
                )
            from autoskillit.core import (
                compute_analysis,
                filter_sessions_by_recipe,
                format_top_bigrams,
                parse_sessions_from_summary_dir,
                render_adjacency_table,
                render_dot,
                render_mermaid,
            )

            log_root = _get_log_root()
            sessions = list(parse_sessions_from_summary_dir(log_root))
            if recipe:
                sessions = filter_sessions_by_recipe(sessions, recipe)
            result = compute_analysis(sessions)
            dfg = (
                result.global_dfg
                if not recipe
                else result.by_recipe.get(recipe, result.global_dfg)
            )

            if format == "mermaid":
                rendering = render_mermaid(dfg, min_count=min_count, top_n=top_n)
            elif format == "dot":
                rendering = render_dot(dfg, min_count=min_count, top_n=top_n)
            else:
                rendering = render_adjacency_table(dfg, top_n=top_n)

            return json.dumps(
                {
                    "success": True,
                    "session_count": result.session_count,
                    "recipe_count": len(result.by_recipe),
                    "top_bigrams": format_top_bigrams(dfg, top_n, min_count),
                    "rendering": rendering,
                }
            )
    except Exception as exc:
        logger.error("analyze_tool_sequences unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _read_quota_events(log_root: Path, n: int) -> tuple[list[dict], int]:
    """Read the last n events from quota_events.jsonl (most recent first).

    Returns (events_slice, total_count). Silently tolerates missing or corrupt lines.
    """
    qe_path = Path(log_root) / "quota_events.jsonl"
    if not qe_path.exists():
        return [], 0
    events: list[dict] = []
    try:
        for line in qe_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return [], 0
    total = len(events)
    return list(reversed(events))[:n], total  # most recent first


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "telemetry", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("get_quota_events")
async def get_quota_events(n: int = 50) -> str:
    """Return the most recent quota guard events from the diagnostic log.

    Events are written by the quota_guard.py PreToolUse hook each time it
    approves or blocks a run_skill call. Use this to diagnose quota throttling
    during long pipeline runs.

    Returns JSON with:
      - events: list of {ts, event, effective_threshold?, window_name?,
                         utilization?, sleep_seconds?, resets_at?, cache_path?}
                         (most recent first)
      - total_count: int  (total events in the log, before limiting to n)

    Args:
        n: Maximum number of events to return (default 50).

    This tool requires the kitchen to be open (gated by open_kitchen).
    This tool sends no MCP progress notifications.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="get_quota_events"):
            from autoskillit.server import _get_ctx

            ctx = _get_ctx()
            log_root = resolve_log_dir(ctx.config.linux_tracing.log_dir)
            events, total = _read_quota_events(log_root, n)
            return json.dumps({"events": events, "total_count": total})
    except Exception as exc:
        logger.error("get_quota_events unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "telemetry"},
    annotations={"readOnlyHint": True},
)
@track_response_size("write_telemetry_files")
async def write_telemetry_files(
    output_dir: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Write token and timing telemetry summaries as markdown files.

    Reads the current session's token log and timing log and writes two
    markdown files to output_dir, creating the directory if needed.
    Files contain PR-ready markdown tables via TelemetryFormatter.

    Returns JSON with:
      - token_summary_path: absolute path to the written token_summary.md
      - timing_summary_path: absolute path to the written timing_summary.md
    On gate closed: {"success": false, "subtype": "gate_error", ...}

    Args:
        output_dir: Directory to write the markdown files into.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(
            tool="write_telemetry_files", output_dir=output_dir
        ):
            logger.info("write_telemetry_files", output_dir=output_dir)
            await _notify(
                ctx,
                "info",
                f"write_telemetry_files: {output_dir}",
                "autoskillit.write_telemetry_files",
                extra={"output_dir": output_dir},
            )

            from autoskillit.server import _get_ctx

            tool_ctx = _get_ctx()
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)

            token_steps = _merge_wall_clock_seconds(
                tool_ctx.token_log.get_report(), tool_ctx.timing_log.get_report()
            )
            token_total = tool_ctx.token_log.compute_total()

            token_path = out / "token_summary.md"
            atomic_write(
                token_path, TelemetryFormatter.format_token_table(token_steps, token_total)
            )

            timing_path = out / "timing_summary.md"
            atomic_write(
                timing_path,
                TelemetryFormatter.format_timing_table(
                    tool_ctx.timing_log.get_report(), tool_ctx.timing_log.compute_total()
                ),
            )

            mcp_path = out / "mcp_response_metrics.json"
            mcp_data = {
                "steps": tool_ctx.response_log.get_report(),
                "total": tool_ctx.response_log.compute_total(),
            }
            atomic_write(mcp_path, json.dumps(mcp_data, indent=2))

            return json.dumps(
                {
                    "token_summary_path": str(token_path),
                    "timing_summary_path": str(timing_path),
                    "mcp_response_metrics_path": str(mcp_path),
                }
            )
    except Exception as exc:
        logger.error("write_telemetry_files unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@track_response_size("read_db")
async def read_db(
    db_path: str,
    query: str,
    params: str = "[]",
    timeout: int = 0,
    ctx: Context = CurrentContext(),
) -> str:
    """Run a read-only SQL query against a SQLite database, return JSON.

    Defense-in-depth: regex pre-validation rejects non-SELECT queries, the connection
    is opened with mode=ro (OS-level read-only), and a set_authorizer callback blocks
    any operation other than SELECT/READ/FUNCTION at the engine level.

    Args:
        db_path: Absolute path to the SQLite database file.
        query: SQL SELECT query. Use ? for positional or :name for named placeholders.
        params: JSON-encoded array or object of query parameter values (default "[]").
        timeout: Query timeout in seconds. 0 uses the configured default.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(tool="read_db"):
            logger.info("read_db", db_path=db_path, query=query[:80])
            await _notify(
                ctx,
                "info",
                f"read_db: {query[:80]}",
                "autoskillit.read_db",
                extra={"db_path": db_path},
            )

            # Parse params
            try:
                parsed_params = json.loads(params)
            except json.JSONDecodeError as exc:
                await _notify(
                    ctx,
                    "error",
                    "read_db: invalid params JSON",
                    "autoskillit.read_db",
                    extra={"error": str(exc)},
                )
                return json.dumps({"success": False, "error": f"Invalid params JSON: {exc}"})
            if not isinstance(parsed_params, (list, dict)):
                await _notify(
                    ctx,
                    "error",
                    "read_db: params must be JSON array or object",
                    "autoskillit.read_db",
                    extra={},
                )
                return json.dumps(
                    {"success": False, "error": "params must be a JSON array or object"}
                )

            # Validate db_path
            db = Path(db_path).resolve()
            if not db.exists():
                await _notify(
                    ctx,
                    "error",
                    "read_db: database does not exist",
                    "autoskillit.read_db",
                    extra={"db_path": db_path},
                )
                return json.dumps({"success": False, "error": f"Database does not exist: {db}"})
            if not db.is_file():
                await _notify(
                    ctx,
                    "error",
                    "read_db: path is not a file",
                    "autoskillit.read_db",
                    extra={"db_path": db_path},
                )
                return json.dumps({"success": False, "error": f"Path is not a file: {db}"})

            from autoskillit.server import _get_config, _get_ctx

            tool_ctx = _get_ctx()
            if tool_ctx.db_reader is None:
                return json.dumps({"success": False, "error": "Database reader not configured"})

            # Resolve timeout
            effective_timeout = timeout if timeout > 0 else _get_config().read_db.timeout
            max_rows = _get_config().read_db.max_rows

            # Execute in thread (sqlite3 is blocking)
            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    tool_ctx.db_reader.query,
                    str(db),
                    query,
                    parsed_params,
                    effective_timeout,
                    max_rows,
                )
                return json.dumps(result)
            except ValueError as exc:
                # Non-SELECT SQL rejected by db_reader's defence-in-depth validation
                await _notify(
                    ctx,
                    "error",
                    "read_db: non-SELECT query rejected",
                    "autoskillit.read_db",
                    extra={"error": str(exc)},
                )
                return json.dumps(
                    {
                        "success": False,
                        "error": str(exc),
                        "hint": "Only SELECT queries are allowed",
                    }
                )
            except TimeoutError:
                await _notify(
                    ctx,
                    "error",
                    "read_db: query timed out",
                    "autoskillit.read_db",
                    extra={"timeout": effective_timeout},
                )
                return json.dumps(
                    {"success": False, "error": f"Query exceeded {effective_timeout}s timeout"}
                )
            except Exception as exc:
                logger.warning("read_db query failed", error=type(exc).__name__)
                await _notify(
                    ctx,
                    "error",
                    "read_db: query failed",
                    "autoskillit.read_db",
                    extra={"error": type(exc).__name__},
                )
                return json.dumps({"success": False, "error": f"Query failed: {exc}"})
    except Exception as exc:
        logger.error("read_db unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
