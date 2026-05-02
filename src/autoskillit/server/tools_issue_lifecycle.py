"""MCP tool handlers: prepare_issue, enrich_issues, claim_issue, release_issue."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import RetryReason, _parse_issue_ref, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import _extract_block
from autoskillit.server._notify import _notify, track_response_size

if TYPE_CHECKING:
    from autoskillit.core import SkillResult, WriteBehaviorSpec

logger = get_logger(__name__)

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
    """Assemble the skill_command string for /prepare-issue."""
    parts = [f"/prepare-issue\n\nTitle: {title}\n\nBody:\n{body}"]
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
    """Assemble the skill_command string for /enrich-issues."""
    parts = ["/enrich-issues"]
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

    Launches /prepare-issue in a headless session to perform the
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

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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

        if labels:
            if err := tool_ctx.config.github.check_labels_allowed(labels):
                return json.dumps({"success": False, "error": err})

        skill_command = _build_prepare_skill_command(title, body, repo, labels, dry_run, split)

        expected_output_patterns: list[str] = []
        if tool_ctx.output_pattern_resolver:
            expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

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
    except Exception as exc:
        logger.error("prepare_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


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

    Launches /enrich-issues in a headless session to scan candidate
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

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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
    except Exception as exc:
        logger.error("enrich_issues unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("claim_issue")
async def claim_issue(
    issue_url: str,
    label: str | None = None,
    allow_reentry: bool = False,
) -> str:
    """Apply an in-progress label to a GitHub issue to claim it for processing.

    Checks if the issue already has the label (another session may be processing it),
    ensures the label exists in the repo, then applies it atomically.

    Returns JSON with: success, claimed (bool), issue_number, label.
    When claimed=false, the issue is already being processed by another session.
    When allow_reentry=True and label already present, returns claimed=True with reentry=True.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42)
                   or shorthand (owner/repo#42).
        label: Label name to apply. Defaults to github.in_progress_label from config.
        allow_reentry: When True and the in-progress label is already present, returns
                       claimed=True with reentry=True instead of claimed=False. Used by
                       process-issues to re-enter recipes for upfront-claimed issues.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="claim_issue", issue_url=issue_url)
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

        if err := tool_ctx.config.github.check_label_allowed(effective_label):
            return json.dumps({"success": False, "error": err})

        result = await tool_ctx.github_client.fetch_issue(issue_url, include_comments=False)
        if not result.get("success"):
            return json.dumps({"success": False, "error": result.get("error", "fetch failed")})

        current_labels = _extract_label_names(result.get("labels", []))
        if effective_label in current_labels:
            if allow_reentry:
                return json.dumps(
                    {
                        "success": True,
                        "claimed": True,
                        "reentry": True,
                        "issue_number": issue_number,
                        "label": effective_label,
                    }
                )
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

        swap_result = await tool_ctx.github_client.swap_labels(
            owner,
            repo,
            issue_number,
            remove_labels=[tool_ctx.config.github.fail_label],
            add_labels=[effective_label],
        )
        if not swap_result.get("success"):
            return json.dumps(
                {"success": False, "error": swap_result.get("error", "swap_labels failed")}
            )

        return json.dumps(
            {
                "success": True,
                "claimed": True,
                "issue_number": issue_number,
                "label": effective_label,
            }
        )
    except Exception as exc:
        logger.error("claim_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("release_issue")
async def release_issue(
    issue_url: str,
    label: str | None = None,
    target_branch: str | None = None,
    staged_label: str | None = None,
    fail_label: str | None = None,
) -> str:
    """Remove the in-progress label from a GitHub issue to release it.

    Call this in cleanup paths (both success and failure) to allow the issue
    to be picked up by future pipeline runs.

    When target_branch is provided and differs from the configured default base branch,
    also applies a 'staged' label to indicate the work is merged and awaiting promotion.

    When fail_label is provided (and target_branch is NOT), swaps in-progress for the
    fail label to mark the issue as failed without releasing it back to the queue.

    Returns JSON with: success, issue_number, label, staged, staged_label.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL or shorthand (owner/repo#42).
        label: Label name to remove. Defaults to github.in_progress_label from config.
        target_branch: Branch the PR was merged into. When non-default, applies staged label.
        staged_label: Label name for staged state. Defaults to github.staged_label from config.
        fail_label: Label name for failure state. When provided, swaps in-progress for this label.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="release_issue", issue_url=issue_url)
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

        # Determine if staging is needed
        promotion_target = tool_ctx.config.branching.promotion_target
        should_stage = target_branch is not None and target_branch != promotion_target

        staged = False
        config_fail_label = tool_ctx.config.github.fail_label
        effective_staged_label = staged_label or tool_ctx.config.github.staged_label

        remove_set = [effective_label]
        if config_fail_label:
            remove_set.append(config_fail_label)

        if should_stage:
            if err := tool_ctx.config.github.check_label_allowed(effective_staged_label):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_staged_label,
                        "error": err,
                    }
                )

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

            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=remove_set,
                add_labels=[effective_staged_label],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to apply staged label: {swap_result.get('error', '?')}",
                    }
                )
            staged = True
        elif fail_label is not None:
            if err := tool_ctx.config.github.check_label_allowed(fail_label):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": fail_label,
                        "error": err,
                    }
                )

            ensure_result = await tool_ctx.github_client.ensure_label(
                owner,
                repo,
                fail_label,
                color="d73a4a",
            )
            if not ensure_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": (
                            f"Failed to ensure fail label: {ensure_result.get('error', '?')}"
                        ),
                    }
                )

            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=[effective_label],
                add_labels=[fail_label],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to apply fail label: {swap_result.get('error', '?')}",
                    }
                )

            return json.dumps(
                {
                    "success": True,
                    "issue_number": issue_number,
                    "label": effective_label,
                    "failed": True,
                    "fail_label": fail_label,
                }
            )
        else:
            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=remove_set,
                add_labels=[],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to remove label: {swap_result.get('error', '?')}",
                    }
                )

        return json.dumps(
            {
                "success": True,
                "issue_number": issue_number,
                "label": effective_label,
                "staged": staged,
                "staged_label": effective_staged_label if staged else None,
            }
        )
    except Exception as exc:
        logger.error("release_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
