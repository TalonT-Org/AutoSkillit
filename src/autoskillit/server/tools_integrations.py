"""MCP tool handlers: fetch_github_issue, report_bug, prepare_issue, enrich_issues."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import RetryReason, _parse_issue_ref, atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _notify,
    _require_enabled,
    _run_subprocess,
    resolve_log_dir,
    track_response_size,
)

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher, HeadlessExecutor, SkillResult

logger = get_logger(__name__)

# Safe session ID pattern: alphanumeric + hyphens + underscores, no path traversal sequences.
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")

# Fingerprint block delimiters written by the report-bug skill in its response.
_FINGERPRINT_START = "---bug-fingerprint---"
_FINGERPRINT_END = "---/bug-fingerprint---"

# Result block delimiters written by the prepare-issue skill in its response.
_PREPARE_RESULT_START = "---prepare-issue-result---"
_PREPARE_RESULT_END = "---/prepare-issue-result---"

# Result block delimiters written by the enrich-issues skill in its response.
_ENRICH_RESULT_START = "---enrich-issues-result---"
_ENRICH_RESULT_END = "---/enrich-issues-result---"

# Sentinel error strings returned by _parse_*_result when block extraction fails.
# Shared by prepare_issue and enrich_issues to distinguish parse failures from
# skill-internal errors embedded in a valid block.
_BLOCK_PARSE_ERRORS: frozenset[str] = frozenset(
    {"no result block found", "result block contained invalid JSON"}
)


def _build_headless_error_response(
    result: SkillResult,
    *,
    error: str,
    status: str = "failed",
) -> dict[str, Any]:
    """Canonical error response for tools that invoke headless sessions.

    Every failure path that derives a response from a SkillResult MUST use this
    builder. Do not hand-roll error dicts — that pattern caused silent omission of
    diagnostic fields (issue #384). Adding a field here propagates to all paths
    automatically.
    """
    return {
        "success": False,
        "status": status,
        "error": error,
        "session_id": result.session_id,
        "stderr": result.stderr or "",
        "subtype": result.subtype or "",
        "exit_code": result.exit_code if result.exit_code is not None else -1,
    }


def _retry_reason_to_error(result: SkillResult) -> str:
    """Extract a human-readable error string from a failed SkillResult.

    Uses result.retry_reason.value when retry_reason is a RetryReason enum member
    and not NONE; otherwise falls back to result.subtype or a generic message.
    """
    if isinstance(result.retry_reason, RetryReason) and result.retry_reason not in (
        RetryReason.NONE,
        None,
    ):
        return result.retry_reason.value
    return result.subtype or "skill session failed"


# Strong references to in-flight non-blocking report tasks (prevents GC).
_pending_report_tasks: set[asyncio.Task[Any]] = set()


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("fetch_github_issue")
async def fetch_github_issue(
    issue_url: str,
    include_comments: bool = True,
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

    This tool requires the kitchen to be open (gated by open_kitchen).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    from autoskillit.server import _get_config, _get_ctx

    # Read-only query: structlog context binding is intentionally omitted.
    logger.info("fetch_github_issue", issue_url=issue_url, include_comments=include_comments)

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


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("get_issue_title")
async def get_issue_title(issue_url: str) -> str:
    """Fetch only the title and slug for a GitHub issue — no body, no comments.

    Returns JSON with: success, number, title, slug.
    slug is a URL-safe branch-prefix derived from the title
    (lowercased, non-alphanumeric chars replaced with hyphens).

    Use this tool when you need a descriptive branch prefix from an issue title
    without fetching the full issue content.

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42)
                   or shorthand (owner/repo#42).
    """
    if (gate := _require_enabled()) is not None:
        return gate
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
    """
    if (gate := _require_enabled()) is not None:
        return gate

    with structlog.contextvars.bound_contextvars(tool="report_bug", cwd=cwd, severity=severity):
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
                log_dir=log_dir,
                expected_output_patterns=expected_output_patterns,
                write_behavior=write_spec,
            )
        )
        _pending_report_tasks.add(task)
        task.add_done_callback(_pending_report_tasks.discard)
        return json.dumps(
            {"success": True, "status": "dispatched", "report_path": str(report_path)}
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_block(text: str, start_delim: str, end_delim: str) -> list[str]:
    """Return all lines between start_delim and end_delim (exclusive).

    Returns an empty list if either delimiter is absent or the block is empty.
    Lines are returned as-is (no stripping) to preserve JSON-parseable content.
    """
    in_block = False
    block_lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == start_delim:
            in_block = True
            continue
        if line.strip() == end_delim:
            if not in_block:
                return []
            return block_lines
        if in_block:
            block_lines.append(line)
    return []  # end delimiter never found


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
    if not session_id or session_id.startswith(("no_session_", "crashed_")):
        return None

    if not _SAFE_SESSION_ID_RE.match(session_id):
        return None

    try:
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
    except (OSError, json.JSONDecodeError, ValueError):
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
        "",
    ]

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
        diag_section = (
            "\n\n" + _format_diagnostics_section(diag, condensed=True) if diag is not None else ""
        )
        comment_body = (
            f"**New occurrence auto-reported on {date_str}**\n\n"
            f"**Error context:**\n```\n{error_context}\n```\n\n"
            f"**Local report:** `{report_path}`"
            f"{diag_section}"
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
) -> dict[str, Any]:
    """Run the headless session, write the report, and handle GitHub filing.

    Returns a result dict suitable for JSON serialisation. Never raises.
    """
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

    return {
        "success": True,
        "status": "complete",
        "report": report_text,
        "report_path": str(report_path),
        "github": github,
    }


# ---------------------------------------------------------------------------
# Helpers for prepare_issue / enrich_issues
# ---------------------------------------------------------------------------


def _extract_label_names(raw_labels: list[Any]) -> list[str]:
    """Extract label name strings from a mixed list of dicts or strings."""
    return [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in raw_labels]


def _without_success_key(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of d with the 'success' key removed.

    Used when merging parsed skill block data into a response dict where
    result.success is the authoritative success signal — preventing the block's
    own 'success' field from silently overwriting the outer key.
    """
    return {k: v for k, v in d.items() if k != "success"}


def _build_prepare_skill_command(
    title: str,
    body: str,
    repo: str,
    labels: list[str] | None,
    dry_run: bool,
    split: bool,
) -> str:
    """Assemble the skill_command string for /autoskillit:prepare-issue."""
    parts = [f"/autoskillit:prepare-issue\n\nTitle: {title}\n\nBody:\n{body}"]
    if repo:
        parts.append(f"--repo {repo}")
    if labels:
        for lbl in labels:
            parts.append(f"--label {lbl}")
    if dry_run:
        parts.append("--dry-run")
    if split:
        parts.append("--split")
    return "\n".join(parts)


def _parse_prepare_result(response_text: str) -> dict[str, Any]:
    """Extract and JSON-parse the prepare-issue result block from a skill response."""
    block_lines = _extract_block(response_text, _PREPARE_RESULT_START, _PREPARE_RESULT_END)
    if not block_lines:
        return {"success": False, "error": "no result block found"}
    try:
        return json.loads("\n".join(block_lines))
    except json.JSONDecodeError:
        return {"success": False, "error": "result block contained invalid JSON"}


def _build_enrich_skill_command(
    issue_number: int | None,
    batch: int | None,
    dry_run: bool,
    repo: str | None,
) -> str:
    """Assemble the skill_command string for /autoskillit:enrich-issues."""
    parts = ["/autoskillit:enrich-issues"]
    if issue_number is not None:
        parts.append(f"--issue {issue_number}")
    if batch is not None:
        parts.append(f"--batch {batch}")
    if dry_run:
        parts.append("--dry-run")
    if repo:
        parts.append(f"--repo {repo}")
    return "\n".join(parts)


def _parse_enrich_result(response_text: str) -> dict[str, Any]:
    """Extract and JSON-parse the enrich-issues result block from a skill response."""
    block_lines = _extract_block(response_text, _ENRICH_RESULT_START, _ENRICH_RESULT_END)
    if not block_lines:
        return {"success": False, "error": "no result block found"}
    try:
        return json.loads("\n".join(block_lines))
    except json.JSONDecodeError:
        return {"success": False, "error": "result block contained invalid JSON"}


# ---------------------------------------------------------------------------
# New gated MCP tools: prepare_issue, enrich_issues, claim_issue, release_issue
# ---------------------------------------------------------------------------


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("prepare_issue")
async def prepare_issue(
    title: str,
    body: str,
    repo: str = "",
    labels: list[str] | None = None,
    dry_run: bool = False,
    split: bool = False,
    ctx: Context = CurrentContext(),
) -> str:
    """Create a GitHub issue and immediately triage it with LLM classification.

    Launches /autoskillit:prepare-issue in a headless session to perform the
    full triage workflow: dedup check, create or adopt the issue, LLM
    classification (bug vs enhancement, implementation vs remediation route),
    mixed-concern detection, and label application.

    Returns JSON with: success, status, issue_url, issue_number, route,
    issue_type, confidence, rationale, labels_applied, dry_run, sub_issues.
    On gate closed or misconfiguration: {success: false, error: "..."}

    Args:
        title: Issue title.
        body: Issue body — description, acceptance criteria, or error context.
        repo: Target repository as owner/repo. Falls back to gh default repo if empty.
        labels: Additional labels to apply beyond triage labels (optional).
        dry_run: When True, classifies and previews without creating or labeling.
        split: When True, splits mixed-concern issues into sub-issues automatically.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="prepare_issue", title=title[:60])
    logger.info("prepare_issue", title=title[:60], dry_run=dry_run, split=split)
    await _notify(
        ctx,
        "info",
        f"prepare_issue: {title[:60]}",
        "autoskillit.prepare_issue",
        extra={"dry_run": dry_run, "split": split},
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})

    skill_command = _build_prepare_skill_command(title, body, repo, labels, dry_run, split)

    expected_output_patterns: list[str] = []
    if tool_ctx.output_pattern_resolver:
        expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

    from autoskillit.core import WriteBehaviorSpec

    write_spec: WriteBehaviorSpec | None = None
    if tool_ctx.write_expected_resolver:
        write_spec = tool_ctx.write_expected_resolver(skill_command)

    result = await tool_ctx.executor.run(
        skill_command,
        str(Path.cwd()),
        expected_output_patterns=expected_output_patterns,
        write_behavior=write_spec,
    )

    if not result.success:
        return json.dumps(
            _build_headless_error_response(result, error=_retry_reason_to_error(result))
        )

    if result.result is None or not result.result.strip():
        return json.dumps(
            _build_headless_error_response(
                result,
                error="session completed but output was empty (drain race)",
            )
        )

    parsed = _parse_prepare_result(result.result)
    # Distinguish block-parse failures (block absent or malformed JSON) from skill-level data.
    # The sentinel errors from _parse_prepare_result signal a block-extraction failure —
    # these are not the same as skill-internal errors embedded in a valid block.
    if parsed.get("error") in _BLOCK_PARSE_ERRORS:
        return json.dumps(_build_headless_error_response(result, error=parsed["error"]))

    # Block parsed successfully. result.success=True is the authoritative signal —
    # the parsed block's "success" field (if any) must not overwrite it.
    return json.dumps(
        {
            "success": True,
            "status": "complete",
            **_without_success_key(parsed),
        }
    )


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("enrich_issues")
async def enrich_issues(
    issue_number: int | None = None,
    batch: int | None = None,
    dry_run: bool = False,
    repo: str | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Backfill structured requirements on existing recipe:implementation issues.

    Launches /autoskillit:enrich-issues in a headless session to scan candidate
    issues, filter out already-enriched ones, perform codebase-grounded analysis,
    and append a Requirements section in REQ-{GRP}-NNN format via gh issue edit.

    Complements prepare_issue (which enriches at creation time) by handling the
    pre-existing backlog.

    Returns JSON with: enriched[], skipped_already_enriched[], skipped_too_vague[],
    skipped_mixed_concerns[], dry_run.
    On gate closed or skill failure: {success: false, status: "failed", error: "...",
    session_id, stderr, subtype, exit_code} (unified contract via _build_headless_error_response).

    Args:
        issue_number: Enrich a single issue by number (optional).
        batch: Filter candidates by batch:N label in addition to recipe:implementation.
        dry_run: When True, previews generated requirements without editing issues.
        repo: Target repository as owner/repo. Falls back to gh default repo if None.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        tool="enrich_issues",
        issue_number=issue_number,
        batch=batch,
        dry_run=dry_run,
    )
    logger.info("enrich_issues", issue_number=issue_number, batch=batch, dry_run=dry_run)
    await _notify(
        ctx,
        "info",
        "enrich_issues: backfilling requirements on recipe:implementation issues",
        "autoskillit.enrich_issues",
        extra={"dry_run": dry_run},
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})

    skill_command = _build_enrich_skill_command(issue_number, batch, dry_run, repo)

    expected_output_patterns: list[str] = []
    if tool_ctx.output_pattern_resolver:
        expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

    from autoskillit.core import WriteBehaviorSpec

    write_spec: WriteBehaviorSpec | None = None
    if tool_ctx.write_expected_resolver:
        write_spec = tool_ctx.write_expected_resolver(skill_command)

    result = await tool_ctx.executor.run(
        skill_command,
        str(Path.cwd()),
        expected_output_patterns=expected_output_patterns,
        write_behavior=write_spec,
    )

    if not result.success:
        return json.dumps(
            _build_headless_error_response(result, error=_retry_reason_to_error(result))
        )

    if result.result is None or not result.result.strip():
        return json.dumps(
            _build_headless_error_response(
                result,
                error="session completed but output was empty (drain race)",
            )
        )

    parsed = _parse_enrich_result(result.result)
    if parsed.get("error") in _BLOCK_PARSE_ERRORS:
        return json.dumps(_build_headless_error_response(result, error=parsed["error"]))

    return json.dumps(
        {
            "success": True,
            "status": "complete",
            **_without_success_key(parsed),
        }
    )


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("claim_issue")
async def claim_issue(
    issue_url: str,
    label: str | None = None,
) -> str:
    """Apply an in-progress label to a GitHub issue to claim it for processing.

    Checks if the issue already has the label (another session may be processing it),
    ensures the label exists in the repo, then applies it atomically.

    Returns JSON with: success, claimed (bool), issue_number, label.
    When claimed=false, the issue is already being processed by another session.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42)
                   or shorthand (owner/repo#42).
        label: Label name to apply. Defaults to github.in_progress_label from config.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    with structlog.contextvars.bound_contextvars(tool="claim_issue", issue_url=issue_url):
        logger.info("claim_issue", issue_url=issue_url)

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.github_client is None:
            return json.dumps(
                {"success": False, "error": "GitHub token required for label management"}
            )

        effective_label = label or tool_ctx.config.github.in_progress_label

        try:
            owner, repo, issue_number = _parse_issue_ref(issue_url)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        result = await tool_ctx.github_client.fetch_issue(issue_url, include_comments=False)
        if not result.get("success"):
            return json.dumps({"success": False, "error": result.get("error", "fetch failed")})

        current_labels = _extract_label_names(result.get("labels", []))
        if effective_label in current_labels:
            return json.dumps(
                {
                    "success": True,
                    "claimed": False,
                    "reason": (
                        f"Issue #{issue_number} already has '{effective_label}' label"
                        " — another session may be processing it"
                    ),
                }
            )

        await tool_ctx.github_client.ensure_label(
            owner,
            repo,
            effective_label,
            color="fbca04",
            description="Issue is actively being processed by a pipeline session",
        )

        add_result = await tool_ctx.github_client.add_labels(
            owner, repo, issue_number, [effective_label]
        )
        if not add_result.get("success"):
            return json.dumps(
                {"success": False, "error": add_result.get("error", "add_labels failed")}
            )

        return json.dumps(
            {
                "success": True,
                "claimed": True,
                "issue_number": issue_number,
                "label": effective_label,
            }
        )


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("release_issue")
async def release_issue(
    issue_url: str,
    label: str | None = None,
    target_branch: str | None = None,
    staged_label: str | None = None,
) -> str:
    """Remove the in-progress label from a GitHub issue to release it.

    Call this in cleanup paths (both success and failure) to allow the issue
    to be picked up by future pipeline runs.

    When target_branch is provided and differs from the configured default base branch,
    also applies a 'staged' label to indicate the work is merged and awaiting promotion.

    Returns JSON with: success, issue_number, label, staged, staged_label.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL or shorthand (owner/repo#42).
        label: Label name to remove. Defaults to github.in_progress_label from config.
        target_branch: Branch the PR was merged into. When non-default, applies staged label.
        staged_label: Label name for staged state. Defaults to github.staged_label from config.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    with structlog.contextvars.bound_contextvars(tool="release_issue", issue_url=issue_url):
        logger.info("release_issue", issue_url=issue_url)

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.github_client is None:
            return json.dumps(
                {"success": False, "error": "GitHub token required for label management"}
            )

        effective_label = label or tool_ctx.config.github.in_progress_label

        try:
            owner, repo, issue_number = _parse_issue_ref(issue_url)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        result = await tool_ctx.github_client.remove_label(
            owner, repo, issue_number, effective_label
        )
        if not result.get("success", False):
            return json.dumps(
                {
                    "success": False,
                    "issue_number": issue_number,
                    "label": effective_label,
                    "staged": False,
                    "staged_label": None,
                }
            )

        # Determine if staging is needed
        promotion_target = tool_ctx.config.branching.promotion_target
        should_stage = target_branch is not None and target_branch != promotion_target

        staged = False
        effective_staged_label = staged_label or tool_ctx.config.github.staged_label

        if should_stage:
            ensure_result = await tool_ctx.github_client.ensure_label(
                owner,
                repo,
                effective_staged_label,
                color="0075ca",
                description=(
                    f"Implementation staged and waiting for promotion to {promotion_target}"
                ),
            )
            if not ensure_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": (
                            f"Failed to ensure staged label: {ensure_result.get('error', '?')}"
                        ),
                    }
                )

            apply_result = await tool_ctx.github_client.add_labels(
                owner, repo, issue_number, [effective_staged_label]
            )
            if not apply_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to apply staged label: {apply_result.get('error', '?')}",
                    }
                )
            staged = True

        return json.dumps(
            {
                "success": True,
                "issue_number": issue_number,
                "label": effective_label,
                "staged": staged,
                "staged_label": effective_staged_label if staged else None,
            }
        )


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
    for num in issue_numbers:
        cmd = ["gh", "issue", "close", str(num)]
        if comment:
            cmd.extend(["--comment", comment])
        rc, _, _ = await _run_subprocess(cmd, cwd=cwd, timeout=30)
        if rc == 0:
            closed.append(num)
        else:
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
    """
    if (gate := _require_enabled()) is not None:
        return gate

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
    """
    if (gate := _require_enabled()) is not None:
        return gate

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
