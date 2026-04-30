"""MCP tool handlers: get_pr_reviews, bulk_close_issues."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.helpers import (
    _notify,
    track_response_size,
)

logger = get_logger(__name__)


def _map_api_reviews(raw: list) -> list:
    """Map gh api pulls/{n}/reviews response (user.login) to {author, state, body}."""
    return [
        {
            "author": (r.get("user") or {}).get("login", ""),
            "state": r["state"],
            "body": r.get("body", ""),
        }
        for r in raw
    ]


def _map_pr_view_reviews(data: dict) -> list:
    """Map gh pr view --json reviews response (author.login) to {author, state, body}."""
    return [
        {
            "author": (r.get("author") or {}).get("login", ""),
            "state": r["state"],
            "body": r.get("body", ""),
        }
        for r in data.get("reviews", [])
    ]


async def _close_issues_sequentially(
    issue_numbers: list[int],
    comment: str,
    cwd: str,
) -> tuple[list[int], list[int]]:
    """Run gh issue close for each number; return (closed, failed) lists."""
    closed: list[int] = []
    failed: list[int] = []
    for i, num in enumerate(issue_numbers):
        if i > 0:
            await asyncio.sleep(1)
        try:
            if comment:
                rc, body_out, _ = await _run_subprocess(
                    ["gh", "issue", "view", str(num), "--json", "body", "--jq", ".body"],
                    cwd=cwd,
                    timeout=30,
                )
                if rc != 0:
                    failed.append(num)
                    continue
                current_body = body_out.strip()
                if current_body == "null":
                    current_body = ""
                new_body = current_body + f"\n\n---\n\n## Closing Note\n\n{comment}"

                temp_dir = Path(cwd) / ".autoskillit" / "temp" / "bulk-close-issues"
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_file = temp_dir / f"{num}_{int(time.time() * 1000)}_close_body.md"
                atomic_write(temp_file, new_body)

                rc2, _, _ = await _run_subprocess(
                    ["gh", "issue", "edit", str(num), "--body-file", str(temp_file)],
                    cwd=cwd,
                    timeout=30,
                )
                if rc2 != 0:
                    failed.append(num)
                    continue
                await asyncio.sleep(1)

            rc3, _, _ = await _run_subprocess(
                ["gh", "issue", "close", str(num)],
                cwd=cwd,
                timeout=30,
            )
            if rc3 == 0:
                closed.append(num)
            else:
                failed.append(num)
        except Exception:
            logger.warning("Failed to close issue %s", num, exc_info=True)
            failed.append(num)
    return closed, failed


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("get_pr_reviews")
async def get_pr_reviews(
    pr_number: int,
    cwd: str,
    repo: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Fetch reviews for a GitHub pull request as a structured list.

    When repo is provided, calls gh api repos/{repo}/pulls/{pr_number}/reviews
    (returns raw API list with user.login). When repo is omitted, calls
    gh pr view {pr_number} --json reviews (returns author.login).

    Returns JSON with:
      - reviews: list of {author, state, body}
    On gh failure: {"success": false, "error": "..."}

    Args:
        pr_number: GitHub pull request number.
        cwd: Working directory for gh commands.
        repo: Repository as owner/repo. Uses gh api path when provided;
              uses gh pr view when omitted.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="get_pr_reviews", cwd=cwd):
            logger.info("get_pr_reviews", pr_number=pr_number, repo=repo)
            await _notify(
                ctx,
                "info",
                f"get_pr_reviews: #{pr_number}",
                "autoskillit.get_pr_reviews",
                extra={"repo": repo},
            )

            if repo:
                cmd = ["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews"]
                rc, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=30)
                if rc != 0:
                    return json.dumps(
                        {"success": False, "error": stderr.strip() or "gh command failed"}
                    )
                try:
                    raw = json.loads(stdout)
                except json.JSONDecodeError:
                    return json.dumps({"success": False, "error": "Failed to parse gh output"})
                reviews = _map_api_reviews(raw)
            else:
                cmd = ["gh", "pr", "view", str(pr_number), "--json", "reviews"]
                rc, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=30)
                if rc != 0:
                    return json.dumps(
                        {"success": False, "error": stderr.strip() or "gh command failed"}
                    )
                try:
                    data = json.loads(stdout)
                except json.JSONDecodeError:
                    return json.dumps({"success": False, "error": "Failed to parse gh output"})
                reviews = _map_pr_view_reviews(data)

            return json.dumps({"reviews": reviews})
    except Exception as exc:
        logger.error("get_pr_reviews unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("bulk_close_issues")
async def bulk_close_issues(
    issue_numbers: list[int],
    comment: str,
    cwd: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Close multiple GitHub issues, optionally with a comment.

    Runs gh issue close for each number in sequence. Tracks which issues
    closed successfully and which failed.

    Returns JSON with:
      - closed: list of issue numbers that closed successfully
      - failed: list of issue numbers where gh returned non-zero
    On gate closed: {"success": false, "subtype": "gate_error", ...}

    Args:
        issue_numbers: List of GitHub issue numbers to close.
        comment: Optional comment to post when closing. Omitted when empty.
        cwd: Working directory for gh commands.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="bulk_close_issues", cwd=cwd):
            logger.info("bulk_close_issues", count=len(issue_numbers))
            await _notify(
                ctx,
                "info",
                f"bulk_close_issues: {len(issue_numbers)} issue(s)",
                "autoskillit.bulk_close_issues",
                extra={},
            )

            closed, failed = await _close_issues_sequentially(issue_numbers, comment, cwd)
            return json.dumps({"closed": closed, "failed": failed})
    except Exception as exc:
        logger.error("bulk_close_issues unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
