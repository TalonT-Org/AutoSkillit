"""MCP tool handlers: fetch_github_issue."""

from __future__ import annotations

import json

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled

logger = get_logger(__name__)


@mcp.tool(tags={"automation"})
async def fetch_github_issue(
    issue_url: str,
    include_comments: bool = True,
    ctx: Context = CurrentContext(),
) -> str:
    """Retrieve a GitHub issue as a formatted Markdown string.

    Use this tool automatically whenever you encounter a GitHub issue URL,
    shorthand reference (owner/repo#number), or bare issue number (when
    default_repo is configured). Do not ask the user to paste the issue
    content manually.

    Returns JSON with: success, issue_number, title, url, state, labels,
    and content (Markdown). The content field is suitable for passing directly
    as a prompt argument to skills like /autoskillit:make-plan,
    /autoskillit:make-groups, or /autoskillit:investigate.

    On failure: {"success": false, "error": "..."} — never raises.

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42),
                   shorthand (owner/repo#42), or bare issue number when
                   github.default_repo is configured in .autoskillit/config.yaml.
        include_comments: Include the ## Comments section in content (default: true).
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="fetch_github_issue", issue_url=issue_url)
    logger.info("fetch_github_issue", issue_url=issue_url, include_comments=include_comments)
    await _notify(
        ctx,
        "info",
        f"fetch_github_issue: {issue_url}",
        "autoskillit.fetch_github_issue",
        extra={"issue_url": issue_url},
    )

    from autoskillit.server import _get_config, _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.github_client is None:
        return json.dumps({"success": False, "error": "GitHub client not configured"})

    config = _get_config()

    # Resolve bare issue numbers using default_repo from config
    resolved_ref = issue_url
    if issue_url.strip().isdigit():
        if not config.github.default_repo:
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Cannot resolve bare issue number {issue_url!r}: "
                        "github.default_repo is not set in .autoskillit/config.yaml"
                    ),
                }
            )
        resolved_ref = f"{config.github.default_repo}#{issue_url.strip()}"
        logger.info("resolved bare number", resolved_ref=resolved_ref)

    result = await tool_ctx.github_client.fetch_issue(
        resolved_ref,
        include_comments=include_comments,
    )

    if not result.get("success"):
        await _notify(
            ctx,
            "error",
            "fetch_github_issue failed",
            "autoskillit.fetch_github_issue",
            extra={"error": result.get("error", "unknown")},
        )

    return json.dumps(result)
