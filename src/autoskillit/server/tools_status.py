"""MCP tool handlers: kitchen_status, get_pipeline_report, get_token_summary, read_db."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core.logging import get_logger
from autoskillit.execution.db import _execute_readonly_query, _validate_select_only
from autoskillit.server import mcp
from autoskillit.server.helpers import _require_enabled

logger = get_logger(__name__)


@mcp.tool(tags={"automation"})
async def kitchen_status() -> str:
    """Return version health and configuration status for the running server.

    Reports package version, plugin.json version, version match status,
    tools enabled state, and active configuration summary. Call this after
    enabling tools or anytime you need to verify the server is healthy.

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).
    """
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
    return json.dumps(status)


@mcp.tool(tags={"automation"})
async def get_pipeline_report(clear: bool = False) -> str:
    """Return accumulated run_skill / run_skill_retry failures since last clear.

    Orchestrators should call this at the end of a pipeline run to retrieve
    a structured summary of every non-success result. Pass clear=True to
    atomically retrieve and reset the store for the next pipeline run.

    Returns JSON with:
      - total_failures: int
      - failures: list of {timestamp, skill_command, exit_code, subtype,
                            needs_retry, retry_reason, stderr}

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).
    """
    from autoskillit.server import _get_ctx

    report = _get_ctx().audit.get_report()
    if clear:
        _get_ctx().audit.clear()
    return json.dumps(
        {
            "total_failures": len(report),
            "failures": [r.to_dict() for r in report],
        }
    )


@mcp.tool(tags={"automation"})
async def get_token_summary(clear: bool = False) -> str:
    """Return accumulated run_skill/run_skill_retry token usage grouped by step name.

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).

    Returns JSON with:
    - steps: list of {step_name, input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       invocation_count}
    - total: {input_tokens, output_tokens, cache_creation_input_tokens,
               cache_read_input_tokens}

    Args:
        clear: If True, reset the token log after returning current data.
    """
    from autoskillit.server import _get_ctx

    steps = _get_ctx().token_log.get_report()
    if clear:
        _get_ctx().token_log.clear()
    total: dict[str, int] = {
        "input_tokens": sum(s["input_tokens"] for s in steps),
        "output_tokens": sum(s["output_tokens"] for s in steps),
        "cache_creation_input_tokens": sum(s["cache_creation_input_tokens"] for s in steps),
        "cache_read_input_tokens": sum(s["cache_read_input_tokens"] for s in steps),
    }
    return json.dumps({"steps": steps, "total": total})


@mcp.tool(tags={"automation"})
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
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="read_db")
    logger.info("read_db", db_path=db_path, query=query[:80])
    try:
        await ctx.info(
            f"read_db: {query[:80]}",
            logger_name="autoskillit.read_db",
            extra={"db_path": db_path},
        )
    except (RuntimeError, AttributeError):
        pass

    # Parse params
    try:
        parsed_params = json.loads(params)
    except json.JSONDecodeError as exc:
        try:
            await ctx.error(
                "read_db: invalid params JSON",
                logger_name="autoskillit.read_db",
                extra={"error": str(exc)},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Invalid params JSON: {exc}"})
    if not isinstance(parsed_params, (list, dict)):
        try:
            await ctx.error(
                "read_db: params must be JSON array or object",
                logger_name="autoskillit.read_db",
                extra={},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": "params must be a JSON array or object"})

    # Validate db_path
    db = Path(db_path).resolve()
    if not db.exists():
        try:
            await ctx.error(
                "read_db: database does not exist",
                logger_name="autoskillit.read_db",
                extra={"db_path": db_path},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Database does not exist: {db}"})
    if not db.is_file():
        try:
            await ctx.error(
                "read_db: path is not a file",
                logger_name="autoskillit.read_db",
                extra={"db_path": db_path},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Path is not a file: {db}"})

    # SQL validation (regex pre-check)
    try:
        _validate_select_only(query)
    except ValueError as exc:
        try:
            await ctx.error(
                "read_db: non-SELECT query rejected",
                logger_name="autoskillit.read_db",
                extra={"error": str(exc)},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": str(exc), "hint": "Only SELECT queries are allowed"})

    from autoskillit.server import _get_config

    # Resolve timeout
    effective_timeout = timeout if timeout > 0 else _get_config().read_db.timeout
    max_rows = _get_config().read_db.max_rows

    # Execute in thread (sqlite3 is blocking)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _execute_readonly_query,
            str(db),
            query,
            parsed_params,
            effective_timeout,
            max_rows,
        )
        return json.dumps(result)
    except TimeoutError:
        try:
            await ctx.error(
                "read_db: query timed out",
                logger_name="autoskillit.read_db",
                extra={"timeout": effective_timeout},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Query exceeded {effective_timeout}s timeout"})
    except Exception as exc:
        logger.warning("read_db query failed", error=type(exc).__name__)
        try:
            await ctx.error(
                "read_db: query failed",
                logger_name="autoskillit.read_db",
                extra={"error": type(exc).__name__},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Query failed: {exc}"})
