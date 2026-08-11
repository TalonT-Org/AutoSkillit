"""MCP tool handlers: prepare_issue, enrich_issues (headless session tools)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import regex as re
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import RetryReason, _parse_issue_ref, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import _extract_block
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server.tools._backend_compat import _prepare_direct_skill_dispatch
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

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

# Canonical fields of the headless response envelope. extra_fields cannot overwrite
# any of these — see _build_headless_error_response for the contract.
_CANONICAL_KEYS: frozenset[str] = frozenset(
    {"success", "status", "error", "warning", "session_id", "stderr", "subtype", "exit_code"}
)

# Regex for partial-issue-data extraction: a GitHub issue URL anywhere in the
# session's full text output. Used to recover evidence of side effects that
# already happened (gh issue create) even when the structured output block is
# missing or malformed.
_ISSUE_URL_RE: re.Pattern[str] = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/(\d+)"
)

# Regex for partial-enrich-data extraction: matches "Enriched issue #NNN" prose
# emitted by the enrich-issues skill before its structured output block.
_ENRICHED_ISSUE_RE: re.Pattern[str] = re.compile(r"Enriched issue #(\d+)")


def _build_headless_error_response(
    result: SkillResult,
    *,
    error: str | None = None,
    warning: str | None = None,
    status: str = "failed",
    success: bool = False,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical response envelope for tools that invoke headless sessions.

    Every path that derives a response from a SkillResult MUST use this builder —
    including degraded-success paths (``success=True``, ``status="degraded"``). Do
    not hand-roll response dicts — that pattern caused silent omission of
    diagnostic fields (issue #384). Adding a field here propagates to all paths
    automatically.

    Callers may pass ``extra_fields`` to attach partial-result evidence (e.g.,
    ``partial_issue_url`` when a headless session created an issue but failed to
    emit a parseable result block). ``extra_fields`` is filtered against
    ``_CANONICAL_KEYS`` — it cannot overwrite any canonical field.
    """
    resp: dict[str, Any] = {
        "success": success,
        "status": status,
        "session_id": result.session_id,
        "stderr": result.stderr or "",
        "subtype": result.subtype or "",
        "exit_code": result.exit_code if result.exit_code is not None else -1,
    }
    if error is not None:
        resp["error"] = error
    if warning is not None:
        resp["warning"] = warning
    if extra_fields:
        resp.update({k: v for k, v in extra_fields.items() if k not in _CANONICAL_KEYS})
    return resp


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


def _extract_partial_issue_data(result_text: str) -> dict[str, Any]:
    """Mine a failed session's full text output for evidence of a created issue.

    The prepare-issue skill creates a GitHub issue at Step 5, then applies
    classification/labels/requirements on later steps. If the session fails mid-
    execution (CONTRACT_RECOVERY, drain race, malformed block), the caller needs
    to know which issue was created so it does not re-create it.

    Strategy:
      1. Try _parse_prepare_result to find a successfully-parsed block; if the
         block parsed, return its issue_url/issue_number as partial fields.
      2. If the block is present but JSON is malformed, the issue may still
         have been created — search the full text for a GitHub issue URL via
         _ISSUE_URL_RE and return the FIRST match.
      3. If no URL is found anywhere, return an empty dict (no partial data).
    """
    if not result_text:
        return {}

    parsed = _parse_prepare_result(result_text)
    if "error" not in parsed and (parsed.get("issue_url") or parsed.get("issue_number")):
        out: dict[str, Any] = {}
        if "issue_url" in parsed:
            out["partial_issue_url"] = parsed["issue_url"]
        if "issue_number" in parsed:
            out["partial_issue_number"] = parsed["issue_number"]
        return out

    m = _ISSUE_URL_RE.search(result_text)
    if m:
        number = int(m.group(1))
        url = m.group(0)
        return {"partial_issue_url": url, "partial_issue_number": number}
    return {}


def _extract_partial_enrich_data(result_text: str) -> dict[str, Any]:
    """Mine a failed session's full text output for evidence of enriched issues.

    The enrich-issues skill edits issues in batches; if it fails mid-batch
    (CONTRACT_RECOVERY, drain race, malformed block), the caller needs to know
    which issues were already enriched so it does not re-edit them.

    Strategy:
      1. Try _parse_enrich_result; if the block parsed, return its 'enriched'
         list as partial_issues_enriched.
      2. If the block is absent or malformed, search the full text for
         "Enriched issue #NNN" patterns and return the matched issue numbers
         (in document order, deduplicated).
      3. If no matches, return an empty dict (no partial data).
    """
    if not result_text:
        return {}

    parsed = _parse_enrich_result(result_text)
    if "error" not in parsed and parsed.get("enriched"):
        return {"partial_issues_enriched": list(parsed["enriched"])}

    seen: set[int] = set()
    ordered: list[int] = []
    for m in _ENRICHED_ISSUE_RE.finditer(result_text):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    if ordered:
        return {"partial_issues_enriched": ordered}
    return {}


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
    dry_run: bool,
    split: bool,
) -> str:
    """Assemble the skill_command string for /prepare-issue."""
    parts = [f"/prepare-issue\n\nTitle: {title}\n\nBody:\n{body}"]
    if repo:
        parts.append(f"--repo {repo}")
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


def _add_labels_result_error(
    label_result: object,
    requested_labels: list[str],
) -> str | None:
    if not isinstance(label_result, dict):
        return "GitHub returned a malformed label result"
    if label_result.get("success") is not True:
        error = label_result.get("error")
        return str(error) if error else "GitHub label application failed"
    returned_labels = label_result.get("labels")
    if (
        not isinstance(returned_labels, list)
        or not all(isinstance(label, str) for label in returned_labels)
        or not set(requested_labels).issubset(returned_labels)
    ):
        return "GitHub returned a malformed label result"
    return None


def _merge_applied_labels(existing: object, additions: list[str]) -> list[str]:
    ordered = (
        [label for label in existing if isinstance(label, str)]
        if isinstance(existing, list)
        else []
    )
    return list(dict.fromkeys([*ordered, *additions]))


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


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
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
        with structlog.contextvars.bound_contextvars(tool="prepare_issue", title=title[:60]):
            logger.info("prepare_issue", title=title[:60], dry_run=dry_run, split=split)
            await _notify(
                ctx,
                "info",
                f"prepare_issue: {title[:60]}",
                "autoskillit.prepare_issue",
                extra={"dry_run": dry_run, "split": split},
            )

            from autoskillit.server import (  # circular-break
                _get_ctx,
            )  # circular-break: server-internal circular dependency

            tool_ctx = _get_ctx()
            if tool_ctx.executor is None:
                return json.dumps({"success": False, "error": "Executor not configured"})
            github_client = tool_ctx.github_client

            if labels:
                if err := tool_ctx.config.github.check_labels_allowed(labels):
                    return json.dumps({"success": False, "error": err})
            additional_labels = list(dict.fromkeys(labels or []))
            if additional_labels and not dry_run and github_client is None:
                return json.dumps(
                    {
                        "success": False,
                        "error": "GitHub client not configured for additional label application",
                    }
                )

            skill_command = _build_prepare_skill_command(title, body, repo, dry_run, split)

            expected_output_patterns: list[str] = []
            if tool_ctx.output_pattern_resolver:
                expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

            write_spec: WriteBehaviorSpec | None = None
            if tool_ctx.write_expected_resolver:
                write_spec = tool_ctx.write_expected_resolver(skill_command)

            dispatch, dispatch_error = _prepare_direct_skill_dispatch(
                skill_command,
                tool_ctx.project_dir,
                tool_ctx,
            )
            if dispatch_error is not None or dispatch is None:
                return dispatch_error or json.dumps(
                    {"success": False, "error": "Direct skill dispatch preparation failed"}
                )
            try:
                result = await tool_ctx.executor.run(
                    dispatch.resolved_command,
                    str(dispatch.projection_context.cwd),
                    add_dirs=dispatch.add_dirs,
                    expected_output_patterns=expected_output_patterns,
                    write_behavior=write_spec,
                    capability_contract=dispatch.capability_contract,
                )
            finally:
                dispatch.cleanup(tool_ctx)

            if not result.success:
                extra = _extract_partial_issue_data(result.result) if result.result else {}
                return json.dumps(
                    _build_headless_error_response(
                        result, error=_retry_reason_to_error(result), extra_fields=extra
                    )
                )

            if result.result is None or not result.result.strip():
                return json.dumps(
                    _build_headless_error_response(
                        result,
                        error="session completed but output was empty (drain race)",
                    )
                )

            parsed = _parse_prepare_result(result.result)
            # Distinguish block-parse failures (block absent or malformed JSON)
            # from skill-level data.
            # The sentinel errors from _parse_prepare_result signal a block-extraction failure —
            # these are not the same as skill-internal errors embedded in a valid block.
            if parsed.get("error") in _BLOCK_PARSE_ERRORS:
                extra = _extract_partial_issue_data(result.result)
                return json.dumps(
                    _build_headless_error_response(
                        result,
                        warning=parsed["error"],
                        status="degraded",
                        success=True,
                        extra_fields=extra,
                    )
                )

            if additional_labels and not dry_run:
                issue_url = parsed.get("issue_url")
                issue_number = parsed.get("issue_number")
                try:
                    if not isinstance(issue_url, str) or type(issue_number) is not int:
                        raise ValueError("result lacks a valid issue URL and integer number")
                    owner, issue_repo, url_number = _parse_issue_ref(issue_url)
                    canonical_url = f"https://github.com/{owner}/{issue_repo}/issues/{url_number}"
                    if issue_url.strip() != canonical_url or url_number != issue_number:
                        raise ValueError("result issue URL and issue number are inconsistent")
                except ValueError as exc:
                    return json.dumps(
                        _build_headless_error_response(
                            result,
                            error=f"Additional labels were not applied: {exc}",
                            extra_fields=_without_success_key(parsed),
                        )
                    )

                await asyncio.sleep(1)
                assert github_client is not None
                label_result = await github_client.add_labels(
                    owner,
                    issue_repo,
                    issue_number,
                    additional_labels,
                )
                if error := _add_labels_result_error(label_result, additional_labels):
                    return json.dumps(
                        _build_headless_error_response(
                            result,
                            error=f"Additional labels were not applied: {error}",
                            extra_fields=_without_success_key(parsed),
                        )
                    )

            if additional_labels:
                parsed["labels_applied"] = _merge_applied_labels(
                    parsed.get("labels_applied"),
                    additional_labels,
                )

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
@_cancellation_shield()
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
        with structlog.contextvars.bound_contextvars(
            tool="enrich_issues",
            issue_number=issue_number,
            batch=batch,
            dry_run=dry_run,
        ):
            logger.info("enrich_issues", issue_number=issue_number, batch=batch, dry_run=dry_run)
            await _notify(
                ctx,
                "info",
                "enrich_issues: backfilling requirements on recipe:implementation issues",
                "autoskillit.enrich_issues",
                extra={"dry_run": dry_run},
            )

            from autoskillit.server import (  # circular-break
                _get_ctx,
            )  # circular-break: server-internal circular dependency

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

            dispatch, dispatch_error = _prepare_direct_skill_dispatch(
                skill_command,
                tool_ctx.project_dir,
                tool_ctx,
            )
            if dispatch_error is not None or dispatch is None:
                return dispatch_error or json.dumps(
                    {"success": False, "error": "Direct skill dispatch preparation failed"}
                )
            try:
                result = await tool_ctx.executor.run(
                    dispatch.resolved_command,
                    str(dispatch.projection_context.cwd),
                    add_dirs=dispatch.add_dirs,
                    expected_output_patterns=expected_output_patterns,
                    write_behavior=write_spec,
                    capability_contract=dispatch.capability_contract,
                )
            finally:
                dispatch.cleanup(tool_ctx)

            if not result.success:
                extra = _extract_partial_enrich_data(result.result) if result.result else {}
                return json.dumps(
                    _build_headless_error_response(
                        result, error=_retry_reason_to_error(result), extra_fields=extra
                    )
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
                extra = _extract_partial_enrich_data(result.result)
                return json.dumps(
                    _build_headless_error_response(
                        result,
                        warning=parsed["error"],
                        status="degraded",
                        success=True,
                        extra_fields=extra,
                    )
                )

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
