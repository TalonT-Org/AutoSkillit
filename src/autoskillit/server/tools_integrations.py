"""MCP tool handlers: fetch_github_issue, report_bug."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher, HeadlessExecutor

logger = get_logger(__name__)

# Fingerprint block delimiters written by the report-bug skill in its response.
_FINGERPRINT_START = "---bug-fingerprint---"
_FINGERPRINT_END = "---/bug-fingerprint---"

# Strong references to in-flight non-blocking report tasks (prevents GC).
_pending_report_tasks: set[asyncio.Task[Any]] = set()


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


@mcp.tool(tags={"automation"})
async def report_bug(
    error_context: str,
    cwd: str,
    severity: str = "non_blocking",
    model: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a headless investigation session for a bug and file or update a GitHub issue.

    Launches /autoskillit:report-bug in a headless session to investigate the error,
    produce a structured markdown report, and extract a deduplication fingerprint.
    The report is written to disk. If github.default_repo and a token are configured,
    the tool searches for an existing open issue matching the fingerprint:
      - Duplicate found: posts a comment with the new error context (skipped if already present).
      - No duplicate: creates a new issue with the report as the body.

    severity="non_blocking" returns immediately after dispatching the background task.
    severity="blocking" awaits the full investigation before returning.

    Returns JSON with:
      Non-blocking: {success, status="dispatched", report_path}
      Blocking:     {success, status="complete"|"failed", report, report_path, github}
      On gate closed or misconfiguration: {success: false, error: "..."}

    Args:
        error_context: Error message, traceback, or free-form bug description.
        cwd: Working directory for the headless session.
        severity: "non_blocking" (fire-and-forget) or "blocking" (await completion).
        model: Model override. Empty string = config default.
        step_name: Optional label for token tracking.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="report_bug", cwd=cwd, severity=severity)
    logger.info("report_bug", error_context=error_context[:80], severity=severity)
    await _notify(
        ctx,
        "info",
        f"report_bug: {error_context[:60]}",
        "autoskillit.report_bug",
        extra={"severity": severity, "cwd": cwd},
    )

    from autoskillit.server import _get_config, _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})

    config = _get_config()
    cfg = config.report_bug

    # Resolve and create the report directory up front so the path is stable
    # before the (potentially background) session writes the file.
    report_dir = (
        Path(cfg.report_dir)
        if cfg.report_dir
        else Path(cwd) / ".autoskillit" / "temp" / "bug-reports"
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    report_path = report_dir / f"{timestamp}_report.md"

    effective_model = model or cfg.model or ""
    skill_command = (
        f"/autoskillit:report-bug\n\n"
        f"Error context:\n{error_context}\n\n"
        f"Report output path: {report_path}"
    )

    if severity == "blocking":
        result = await _run_report_session(
            skill_command,
            cwd,
            report_path,
            error_context,
            tool_ctx.executor,
            tool_ctx.github_client,
            config,
            effective_model,
            step_name,
        )
        if not result["success"]:
            await _notify(
                ctx,
                "error",
                "report_bug session failed",
                "autoskillit.report_bug",
                extra={"report_path": str(report_path)},
            )
        return json.dumps(result)

    # Non-blocking: fire and forget, return immediately.
    task = asyncio.create_task(
        _run_report_session(
            skill_command,
            cwd,
            report_path,
            error_context,
            tool_ctx.executor,
            tool_ctx.github_client,
            config,
            effective_model,
            step_name,
        )
    )
    _pending_report_tasks.add(task)
    task.add_done_callback(_pending_report_tasks.discard)
    return json.dumps({"success": True, "status": "dispatched", "report_path": str(report_path)})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_fingerprint(report_text: str) -> str | None:
    """Extract the first non-empty line between fingerprint delimiters."""
    in_block = False
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped == _FINGERPRINT_START:
            in_block = True
            continue
        if stripped == _FINGERPRINT_END:
            break
        if in_block and stripped:
            return stripped
    return None


async def _file_or_update_github_issue(
    fingerprint: str,
    error_context: str,
    report_text: str,
    report_path: Path,
    github_client: GitHubFetcher,
    config: Any,
) -> dict[str, Any]:
    """Search for a duplicate issue; comment if found, create if not.

    Never raises — all errors captured in the returned dict.
    """
    default_repo = config.github.default_repo
    if not default_repo or "/" not in default_repo:
        return {"skipped": True, "reason": "github.default_repo not configured"}

    owner, repo = default_repo.split("/", 1)
    labels = config.report_bug.github_labels

    search_result = await github_client.search_issues(fingerprint, owner, repo)
    if not search_result.get("success"):
        return {"skipped": True, "reason": f"search failed: {search_result.get('error', '?')}"}

    if search_result.get("total_count", 0) > 0:
        existing = search_result["items"][0]
        issue_number: int = existing["number"]
        issue_url: str = existing["html_url"]
        existing_body: str = existing.get("body", "") or ""

        # Skip comment if the exact error_context is already in the issue body.
        if error_context.strip() in existing_body:
            logger.info(
                "report_bug duplicate skipped comment",
                issue=issue_number,
                reason="error_context already present",
            )
            return {"duplicate": True, "issue_url": issue_url, "comment_added": False}

        date_str = datetime.now(UTC).date().isoformat()
        comment_body = (
            f"**New occurrence auto-reported on {date_str}**\n\n"
            f"**Error context:**\n```\n{error_context}\n```\n\n"
            f"**Local report:** `{report_path}`"
        )
        comment_result = await github_client.add_comment(owner, repo, issue_number, comment_body)
        logger.info(
            "report_bug commented on duplicate",
            issue=issue_number,
            comment_success=comment_result.get("success"),
        )
        return {
            "duplicate": True,
            "issue_url": issue_url,
            "comment_added": comment_result.get("success", False),
        }

    # No duplicate — create a new issue.
    create_result = await github_client.create_issue(
        owner,
        repo,
        fingerprint or error_context.splitlines()[0][:80],
        report_text,
        labels=labels,
    )
    logger.info("report_bug created issue", success=create_result.get("success"))
    return {
        "duplicate": False,
        "issue_created": create_result.get("success", False),
        "issue_url": create_result.get("url", ""),
    }


async def _run_report_session(
    skill_command: str,
    cwd: str,
    report_path: Path,
    error_context: str,
    executor: HeadlessExecutor,
    github_client: GitHubFetcher | None,
    config: Any,
    model: str,
    step_name: str,
) -> dict[str, Any]:
    """Run the headless session, write the report, and handle GitHub filing.

    Returns a result dict suitable for JSON serialisation. Never raises.
    """
    cfg = config.report_bug
    skill_result = await executor.run(
        skill_command, cwd, model=model, step_name=step_name, timeout=float(cfg.timeout)
    )

    report_text = skill_result.result or skill_result.stderr or "No report generated."
    try:
        report_path.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        logger.warning("report_bug write failed", path=str(report_path), error=str(exc))

    if not skill_result.success:
        return {
            "success": False,
            "status": "failed",
            "report": report_text,
            "report_path": str(report_path),
        }

    github: dict[str, Any] = {}
    if cfg.github_filing and github_client is not None and github_client.has_token:
        fingerprint = _parse_fingerprint(report_text) or error_context.splitlines()[0][:80]
        github = await _file_or_update_github_issue(
            fingerprint, error_context, report_text, report_path, github_client, config
        )
    elif cfg.github_filing:
        github = {"skipped": True, "reason": "no_token"}

    return {
        "success": True,
        "status": "complete",
        "report": report_text,
        "report_path": str(report_path),
        "github": github,
    }
