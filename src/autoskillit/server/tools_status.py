"""MCP tool handlers: kitchen_status, get_pipeline_report, get_token_summary, read_db."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled

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
    status["quota_guard_enabled"] = _get_config().quota_guard.enabled
    status["github_token_configured"] = _get_config().github.token is not None or bool(
        os.environ.get("GITHUB_TOKEN")
    )
    status["github_default_repo"] = _get_config().github.default_repo
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

    failures = _get_ctx().audit.get_report_as_dicts()
    if clear:
        _get_ctx().audit.clear()
    return json.dumps(
        {
            "total_failures": len(failures),
            "failures": failures,
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
    total = _get_ctx().token_log.compute_total()
    if clear:
        _get_ctx().token_log.clear()
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
    await _notify(
        ctx, "info", f"read_db: {query[:80]}", "autoskillit.read_db", extra={"db_path": db_path}
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
        return json.dumps({"error": f"Invalid params JSON: {exc}"})
    if not isinstance(parsed_params, (list, dict)):
        await _notify(
            ctx,
            "error",
            "read_db: params must be JSON array or object",
            "autoskillit.read_db",
            extra={},
        )
        return json.dumps({"error": "params must be a JSON array or object"})

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
        return json.dumps({"error": f"Database does not exist: {db}"})
    if not db.is_file():
        await _notify(
            ctx,
            "error",
            "read_db: path is not a file",
            "autoskillit.read_db",
            extra={"db_path": db_path},
        )
        return json.dumps({"error": f"Path is not a file: {db}"})

    from autoskillit.server import _get_config, _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.db_reader is None:
        return json.dumps({"error": "Database reader not configured"})

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
        return json.dumps({"error": str(exc), "hint": "Only SELECT queries are allowed"})
    except TimeoutError:
        await _notify(
            ctx,
            "error",
            "read_db: query timed out",
            "autoskillit.read_db",
            extra={"timeout": effective_timeout},
        )
        return json.dumps({"error": f"Query exceeded {effective_timeout}s timeout"})
    except Exception as exc:
        logger.warning("read_db query failed", error=type(exc).__name__)
        await _notify(
            ctx,
            "error",
            "read_db: query failed",
            "autoskillit.read_db",
            extra={"error": type(exc).__name__},
        )
        return json.dumps({"error": f"Query failed: {exc}"})


@mcp.tool(tags={"automation"})
async def check_quota(ctx: Context = CurrentContext()) -> str:
    """Check 5-hour quota utilization and return whether a sleep is needed.

    When quota_guard.enabled=True (on by default) and utilization
    exceeds quota_guard.threshold, returns should_sleep=True with sleep_seconds
    set to the number of seconds until resets_at + buffer_seconds. This tool
    does NOT sleep internally. When should_sleep=True, the orchestrator must
    call run_cmd to sleep before proceeding with run_skill/run_skill_retry.

    Always returns success=True so pipeline routing is unaffected.

    Returns JSON:
        {
            "success": true,
            "should_sleep": bool,
            "sleep_seconds": int,
            "utilization": float | null,
            "resets_at": str | null,
            "error": str          # only present on credential/network failure
        }

    This tool is gated — open_kitchen must be active to call it.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    from autoskillit.server import _get_config

    config = _get_config()

    from autoskillit.server.helpers import check_and_sleep_if_needed

    quota_result = await check_and_sleep_if_needed(config.quota_guard)

    if quota_result.get("should_sleep"):
        await _notify(
            ctx,
            "info",
            "quota above threshold — caller should sleep",
            "autoskillit.check_quota",
            extra={
                "sleep_seconds": quota_result["sleep_seconds"],
                "utilization": quota_result["utilization"],
            },
        )

    return json.dumps({"success": True, **quota_result})
