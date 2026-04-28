"""MCP tool handlers: fetch_github_issue, get_issue_title, report_bug."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import atomic_write, get_logger
from autoskillit.pipeline import write_status
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _extract_block,
    _notify,
    _require_enabled,
    resolve_log_dir,
    track_response_size,
)

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher, HeadlessExecutor

logger = get_logger(__name__)

# Safe session ID pattern: alphanumeric + hyphens + underscores, no path traversal sequences.
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")

# Fingerprint block delimiters written by the report-bug skill in its response.
_FINGERPRINT_START = "---bug-fingerprint---"
_FINGERPRINT_END = "---/bug-fingerprint---"


@mcp.tool(
    tags={"autoskillit", "kitchen", "github", "fleet-dispatch"}, annotations={"readOnlyHint": True}
)
@track_response_size("fetch_github_issue")
async def fetch_github_issue(
    issue_url: str,
    include_comments: bool = True,
) -> str:
    """Retrieve a GitHub issue as a formatted Markdown string.

    Call this tool when your session's role requires reading and acting on
    the full issue content — for example, when writing an implementation
    plan, conducting an investigation, or generating a scope report.

    Do NOT call this tool when your role is to route the issue URL downstream
    as an ingredient (e.g. passing issue_url to dispatch_food_truck). The
    downstream skill session will fetch the issue when it actually needs the
    content; calling it here is wasteful and redundant.

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

    This tool requires the kitchen to be open (gated by open_kitchen).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_config, _get_ctx

        with structlog.contextvars.bound_contextvars(
            tool="fetch_github_issue", issue_url=issue_url
        ):
            logger.info("fetch_github_issue", include_comments=include_comments)

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
            return json.dumps(result)
    except Exception as exc:
        logger.error("fetch_github_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


@mcp.tool(
    tags={"autoskillit", "kitchen", "github", "fleet-dispatch"}, annotations={"readOnlyHint": True}
)
@track_response_size("get_issue_title")
async def get_issue_title(issue_url: str) -> str:
    """Fetch only the title and slug for a GitHub issue — no body, no comments.

    Returns JSON with: success, number, title, slug.
    slug is a URL-safe branch-prefix derived from the title
    (lowercased, non-alphanumeric chars replaced with hyphens).

    Use this tool when you need a descriptive branch prefix from an issue title
    without fetching the full issue content.

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42)
                   or shorthand (owner/repo#42).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_config, _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.github_client is None:
            return json.dumps({"success": False, "error": "GitHub client not available."})

        config = _get_config()
        url = issue_url.strip()
        if url.isdigit():
            if not config.github.default_repo:
                return json.dumps(
                    {
                        "success": False,
                        "error": "Bare issue number requires github.default_repo in config.",
                    }
                )
            url = f"{config.github.default_repo}#{url}"

        result = await tool_ctx.github_client.fetch_title(url)
        return json.dumps(result)
    except Exception as exc:
        logger.error("get_issue_title unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("report_bug")
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

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(
            tool="report_bug", cwd=cwd, severity=severity
        ):
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
                Path(cfg.report_dir) if cfg.report_dir else tool_ctx.temp_dir / "bug-reports"
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

            log_dir = config.linux_tracing.log_dir if config.linux_tracing is not None else ""

            expected_output_patterns: list[str] = []
            if tool_ctx.output_pattern_resolver:
                expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

            from autoskillit.core import WriteBehaviorSpec

            write_spec: WriteBehaviorSpec | None = None
            if tool_ctx.write_expected_resolver:
                write_spec = tool_ctx.write_expected_resolver(skill_command)

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
                    log_dir=log_dir,
                    expected_output_patterns=expected_output_patterns,
                    write_behavior=write_spec,
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

            # Non-blocking: supervised background dispatch, return immediately.
            status_path = report_path.with_suffix(".status.json")
            atomic_write(
                status_path,
                json.dumps(
                    {"status": "pending", "dispatched_at": datetime.now(UTC).isoformat()},
                    indent=2,
                ),
            )
            if tool_ctx.background is None:  # always set by ToolContext.__post_init__
                raise RuntimeError("ToolContext.background not initialized")
            tool_ctx.background.submit(
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
                    log_dir=log_dir,
                    expected_output_patterns=expected_output_patterns,
                    write_behavior=write_spec,
                    status_path=status_path,
                ),
                label=step_name or "report_bug",
            )
            return json.dumps(
                {
                    "success": True,
                    "status": "dispatched",
                    "report_path": str(report_path),
                    "status_path": str(status_path),
                }
            )
    except Exception as exc:
        logger.error("report_bug unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_fingerprint(report_text: str) -> str | None:
    """Extract the first non-empty line between fingerprint delimiters."""
    for line in _extract_block(report_text, _FINGERPRINT_START, _FINGERPRINT_END):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _read_session_diagnostics(session_id: str, log_dir_override: str) -> dict[str, Any] | None:
    """Read session diagnostics from the on-disk log directory.

    Returns a dict with keys: session_id, session_dir, summary, anomalies,
    proc_trace_tail.  Returns None when:
    - session_id is empty
    - session_id is a fallback prefix (no_session_* or crashed_*)
    - the session directory does not exist on disk

    Never raises.
    """
    try:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None

        if not _SAFE_SESSION_ID_RE.match(session_id):
            return None

        log_root = resolve_log_dir(log_dir_override)
        session_dir = log_root / "sessions" / session_id
        if not session_dir.is_dir():
            return None

        summary: dict[str, Any] = {}
        summary_path = session_dir / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text())

        anomalies: list[dict[str, Any]] = []
        anomalies_path = session_dir / "anomalies.jsonl"
        if anomalies_path.is_file():
            for line in anomalies_path.read_text().splitlines():
                if line.strip():
                    anomalies.append(json.loads(line))

        proc_trace_tail: list[dict[str, Any]] = []
        proc_trace_path = session_dir / "proc_trace.jsonl"
        if proc_trace_path.is_file():
            lines = [ln for ln in proc_trace_path.read_text().splitlines() if ln.strip()]
            proc_trace_tail = [json.loads(ln) for ln in lines[-10:]]

        return {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "summary": summary,
            "anomalies": anomalies,
            "proc_trace_tail": proc_trace_tail,
        }
    except Exception:
        logger.warning("report_bug diagnostics read failed", session_id=session_id, exc_info=True)
        return None


def _format_diagnostics_section(diag: dict[str, Any], condensed: bool = False) -> str:
    """Render session diagnostics as a Markdown section for GitHub issue bodies.

    condensed=False (new issues): full section — metrics table + collapsible
    anomaly and proc-trace blocks + local path links.
    condensed=True (duplicate comments): metrics table only, no blocks.
    """
    s = diag["summary"]
    session_id = s.get("session_id", diag["session_id"])
    duration = s.get("duration_seconds")
    duration_str = f"{duration:.1f}s" if duration is not None else "—"
    peak_rss = s.get("peak_rss_kb", 0)
    peak_oom = s.get("peak_oom_score", 0)
    anomaly_count = s.get("anomaly_count", 0)
    termination = s.get("termination_reason", "—")
    exit_code = s.get("exit_code", "—")
    claude_code_log: str = s.get("claude_code_log") or ""
    tracked_comm: str | None = s.get("tracked_comm")
    tracked_comm_drift: bool = bool(s.get("tracked_comm_drift", False))

    lines: list[str] = [
        "## Session Diagnostics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Session ID | `{session_id}` |",
        f"| Duration | {duration_str} |",
        f"| Peak RSS | {peak_rss} KB |",
        f"| Peak OOM Score | {peak_oom} |",
        f"| Anomaly Count | {anomaly_count} |",
        f"| Termination | {termination} |",
        f"| Exit Code | {exit_code} |",
    ]
    if tracked_comm is not None:
        comm_display = f"`{tracked_comm}`"
        if tracked_comm_drift:
            comm_display += " ⚠️ drift"
        lines.append(f"| Tracked Process | {comm_display} |")
    lines.append("")

    if condensed:
        return "\n".join(lines)

    # Anomalies collapsible block
    anomalies = diag.get("anomalies", [])
    if anomalies:
        rows = "\n".join(
            f"| `{a.get('kind', '?')}` | {a.get('severity', '?')} "
            f"| {json.dumps(a.get('detail', {}))} |"
            for a in anomalies
        )
        lines += [
            "<details>",
            f"<summary>Anomalies ({len(anomalies)})</summary>",
            "",
            "| Kind | Severity | Detail |",
            "|------|----------|--------|",
            rows,
            "",
            "</details>",
            "",
        ]

    # Proc trace collapsible block
    proc_trace = diag.get("proc_trace_tail", [])
    if proc_trace:
        lines += [
            "<details>",
            f"<summary>Process Trace (last {len(proc_trace)} snapshots)</summary>",
            "",
            "```json",
            json.dumps(proc_trace, indent=2),
            "```",
            "",
            "</details>",
            "",
        ]

    # Local path links
    lines += [
        "**Local paths:**",
        f"- Session diagnostics: `{diag['session_dir']}`",
    ]
    if claude_code_log:
        lines.append(f"- Claude Code session log: `{claude_code_log}`")
    lines.append("")

    return "\n".join(lines)


async def _file_or_update_github_issue(
    fingerprint: str,
    error_context: str,
    report_text: str,
    report_path: Path,
    github_client: GitHubFetcher,
    config: Any,
    diag: dict[str, Any] | None,
) -> dict[str, Any]:
    """Search for a duplicate issue; comment if found, create if not.

    Never raises — all errors captured in the returned dict.
    """
    try:
        default_repo = config.github.default_repo
        if not default_repo or "/" not in default_repo:
            return {"skipped": True, "reason": "github.default_repo not configured"}

        owner, repo = default_repo.split("/", 1)
        labels = config.report_bug.github_labels
        for lbl in labels:
            if err := config.github.check_label_allowed(lbl):
                return {"skipped": True, "reason": err}

        search_result = await github_client.search_issues(fingerprint, owner, repo)
        if not search_result.get("success"):
            return {"skipped": True, "reason": f"search failed: {search_result.get('error', '?')}"}

        if search_result.get("total_count", 0) > 0:
            existing = search_result["items"][0]
            issue_number: int = existing["number"]
            issue_url: str = existing["html_url"]
            existing_body: str = existing.get("body", "") or ""

            # Skip update if the exact error_context is already in the issue body.
            if error_context.strip() in existing_body:
                logger.info(
                    "report_bug duplicate skipped update",
                    issue=issue_number,
                    reason="error_context already present",
                )
                return {"duplicate": True, "issue_url": issue_url, "body_updated": False}

            date_str = datetime.now(UTC).date().isoformat()
            diag_section = (
                "\n\n" + _format_diagnostics_section(diag, condensed=True)
                if diag is not None
                else ""
            )
            occurrence_section = (
                f"\n\n---\n\n"
                f"## New Occurrence — {date_str}\n\n"
                f"**Error context:**\n```\n{error_context}\n```\n\n"
                f"**Local report:** `{report_path}`"
                f"{diag_section}"
            )
            new_body = existing_body + occurrence_section
            update_result = await github_client.update_issue_body(
                owner, repo, issue_number, new_body
            )
            logger.info(
                "report_bug updated duplicate body",
                issue=issue_number,
                update_success=update_result.get("success"),
            )
            return {
                "duplicate": True,
                "issue_url": issue_url,
                "body_updated": update_result.get("success", False),
            }

        # No duplicate — create a new issue.
        diag_section = (
            "\n\n" + _format_diagnostics_section(diag, condensed=False) if diag is not None else ""
        )
        issue_body = report_text + diag_section
        create_result = await github_client.create_issue(
            owner,
            repo,
            fingerprint or error_context.splitlines()[0][:80],
            issue_body,
            labels=labels,
        )
        logger.info("report_bug created issue", success=create_result.get("success"))
        return {
            "duplicate": False,
            "issue_created": create_result.get("success", False),
            "issue_url": create_result.get("url", ""),
        }
    except Exception as exc:
        logger.error("_file_or_update_github_issue unhandled exception", exc_info=True)
        return {"skipped": True, "reason": f"unexpected error: {exc}"}


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
    log_dir: str,
    expected_output_patterns: list[str] | None = None,
    write_behavior: Any = None,
    status_path: Path | None = None,
) -> dict[str, Any]:
    """Run the headless session, write the report, and handle GitHub filing.

    Returns a result dict suitable for JSON serialisation. Never raises.
    Writes status_path (if provided) with "complete" or "failed" based on outcome.
    """
    try:
        cfg = config.report_bug
        skill_result = await executor.run(
            skill_command,
            cwd,
            model=model,
            step_name=step_name,
            timeout=float(cfg.timeout),
            expected_output_patterns=expected_output_patterns or [],
            write_behavior=write_behavior,
        )

        report_text = skill_result.result or skill_result.stderr or "No report generated."
        try:
            atomic_write(report_path, report_text)
        except OSError as exc:
            logger.warning("report_bug write failed", path=str(report_path), error=str(exc))

        if not skill_result.success:
            write_status(
                status_path,
                "failed",
                error=(skill_result.stderr or skill_result.subtype or "session failed")[:500],
            )
            return {
                "success": False,
                "status": "failed",
                "report": report_text,
                "report_path": str(report_path),
                "session_id": skill_result.session_id,
                "stderr": skill_result.stderr,
                "subtype": skill_result.subtype,
                "exit_code": skill_result.exit_code,
            }

        diag = _read_session_diagnostics(skill_result.session_id, log_dir)

        github: dict[str, Any] = {}
        if cfg.github_filing and github_client is not None and github_client.has_token:
            fingerprint = _parse_fingerprint(report_text) or error_context.splitlines()[0][:80]
            github = await _file_or_update_github_issue(
                fingerprint, error_context, report_text, report_path, github_client, config, diag
            )
        elif cfg.github_filing:
            github = {"skipped": True, "reason": "no_token"}

        write_status(status_path, "complete")
        return {
            "success": True,
            "status": "complete",
            "report": report_text,
            "report_path": str(report_path),
            "github": github,
        }
    except Exception as exc:
        logger.error("_run_report_session unhandled exception", exc_info=True)
        write_status(status_path, "failed", error=str(exc))
        return {
            "success": False,
            "status": "failed",
            "error": str(exc),
            "report_path": str(report_path),
        }
