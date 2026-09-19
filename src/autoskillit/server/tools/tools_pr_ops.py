"""MCP tool handlers: PR review reads, authoritative writes, and issue closure."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    ContainmentError,
    DiffAnchorAuthority,
    GitHubReviewComment,
    GitHubReviewPostResult,
    GitHubReviewRequest,
    ReviewOperationState,
    ReviewResponseClass,
    atomic_write,
    destination_location,
    get_logger,
    is_final_github_review_state,
    is_valid_github_review_head_sha,
    is_valid_github_review_logical_iteration,
    is_valid_github_review_operation_key,
    is_valid_github_review_repository,
    read_stable_contained_bytes,
)
from autoskillit.server import mcp
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.lifecycle._guards import _require_enabled
from autoskillit.server.lifecycle._session_scope import SCOPE_ANY, session_scoped
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _review_post_error(
    *,
    head_sha: str,
    error: str,
    state: ReviewOperationState,
    response_class: ReviewResponseClass,
) -> str:
    return json.dumps(
        GitHubReviewPostResult(
            operation_key="",
            head_sha=head_sha,
            state=state,
            response_class=response_class,
            error=error,
        ).to_dict()
    )


def _get_ctx():
    from autoskillit.server import _get_ctx as get_context  # circular-break

    return get_context()


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


def _load_unique_json_object(data: bytes) -> dict[str, object]:
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise TypeError("review receipt must be a JSON object")
    return value


def _review_verification_result(success: bool) -> str:
    return json.dumps({"reviews_posted": "true" if success else "false"})


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
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
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
    structlog.contextvars.clear_contextvars()
    with structlog.contextvars.bound_contextvars(tool="get_pr_reviews", cwd=cwd):
        try:
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
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("verify_review_receipt")
async def verify_review_receipt(
    cwd: str,
    receipt_path: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    logical_iteration: str,
    mode: str,
    post_state: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Verify a review effect against the server-owned publication ledger.

    Never raises.
    """

    try:
        if (gate := _require_enabled()) is not None:
            return gate
        if mode == "local":
            return _review_verification_result(post_state == "LOCAL")
        if mode != "github":
            return _review_verification_result(False)
        if (
            not Path(cwd).is_absolute()
            or not os.path.isdir(cwd)
            or not is_valid_github_review_repository(repository)
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
            or not is_valid_github_review_head_sha(head_sha)
            or not is_valid_github_review_logical_iteration(logical_iteration)
            or not is_final_github_review_state(post_state)
        ):
            return _review_verification_result(False)
        receipt = Path(receipt_path)
        if not receipt.is_absolute() or receipt.name != f"batch_review_response_{pr_number}.json":
            return _review_verification_result(False)
        root = Path(cwd) / ".autoskillit" / "temp"
        _, receipt_bytes = read_stable_contained_bytes(
            receipt,
            root,
            max_size_bytes=1_000_000,
        )
        payload = _load_unique_json_object(receipt_bytes)
        operation_key = payload.get("operation_key")
        if not is_valid_github_review_operation_key(operation_key):
            return _review_verification_result(False)

        poster = _get_ctx().github_review_poster
        if poster is None:
            return _review_verification_result(False)
        authoritative = poster.verify_receipt(operation_key)
        if authoritative is None:
            return _review_verification_result(False)
        artifact_wire = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        authoritative_wire = json.dumps(
            authoritative.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        identity_matches = (
            authoritative.repository.casefold() == repository.casefold()
            and authoritative.pr_number == pr_number
            and authoritative.head_sha == head_sha
            and authoritative.logical_iteration == logical_iteration
            and authoritative.state.value == post_state
        )
        return _review_verification_result(
            identity_matches and artifact_wire == authoritative_wire
        )
    except Exception:
        logger.error("verify_review_receipt unhandled exception", exc_info=True)
        return _review_verification_result(False)


@mcp.tool(tags={"autoskillit", "headless", "github"}, annotations={"readOnlyHint": True})
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("post_pr_review")
async def post_pr_review(
    cwd: str,
    receipt_path: str,
    anchor_authority_path: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    logical_iteration: str,
    event: str,
    body: str,
    comments: list[dict[str, object]],
    dry_run: bool,
    ctx: Context = CurrentContext(),
) -> str:
    """Publish one idempotent, server-authoritative pull-request review.

    The handler is intentionally headless-visible and is not application-gated.
    It delegates all identity, pacing, reconciliation, and response policy to
    the injected review poster. Never raises.
    """

    structlog.contextvars.clear_contextvars()
    with structlog.contextvars.bound_contextvars(
        tool="post_pr_review", cwd=cwd, repository=repository, pr_number=pr_number
    ):
        try:
            tool_ctx = _get_ctx()
            poster = tool_ctx.github_review_poster
            if poster is None:
                return _review_post_error(
                    head_sha=head_sha,
                    error="github_review_poster is not configured",
                    state=ReviewOperationState.AMBIGUOUS,
                    response_class=ReviewResponseClass.SERVER_ERROR,
                )
            if not os.path.isdir(cwd):
                return _review_post_error(
                    head_sha=head_sha,
                    error="cwd must be an existing directory",
                    state=ReviewOperationState.TERMINAL,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                )
            root = (Path(cwd) / ".autoskillit" / "temp").resolve()
            destination = destination_location(Path(receipt_path))
            if (
                not Path(cwd).is_absolute()
                or not destination.is_relative_to(root)
                or destination.name != f"batch_review_response_{pr_number}.json"
            ):
                return _review_post_error(
                    head_sha=head_sha,
                    error=(
                        "receipt_path must be an absolute contained path under "
                        f"{root} named batch_review_response_{pr_number}.json"
                    ),
                    state=ReviewOperationState.TERMINAL,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                )
            authority_path = Path(anchor_authority_path)
            if (
                not authority_path.is_absolute()
                or not authority_path.is_relative_to(root)
                or ".." in authority_path.parts
                or authority_path.name != f"anchor_authority_{pr_number}.json"
            ):
                return _review_post_error(
                    head_sha=head_sha,
                    error=(
                        "anchor_authority_path must be an absolute contained path under "
                        f"{root} named anchor_authority_{pr_number}.json"
                    ),
                    state=ReviewOperationState.TERMINAL,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                )
            try:
                authority_path.lstat()
            except FileNotFoundError:
                try:
                    authority = DiffAnchorAuthority.unavailable(
                        repository=repository,
                        pr_number=pr_number,
                        head_sha=head_sha,
                    )
                except (TypeError, ValueError) as exc:
                    return _review_post_error(
                        head_sha=head_sha,
                        error=f"invalid review identity: {exc}",
                        state=ReviewOperationState.TERMINAL,
                        response_class=ReviewResponseClass.CLIENT_ERROR,
                    )
            except OSError as exc:
                return _review_post_error(
                    head_sha=head_sha,
                    error=f"invalid anchor authority path: {exc}",
                    state=ReviewOperationState.TERMINAL,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                )
            else:
                try:
                    _, authority_bytes = read_stable_contained_bytes(
                        authority_path,
                        root,
                        max_size_bytes=1_000_000,
                    )
                    authority = DiffAnchorAuthority.from_wire(
                        _load_unique_json_object(authority_bytes)
                    )
                except (ContainmentError, OSError) as exc:
                    return _review_post_error(
                        head_sha=head_sha,
                        error=f"invalid anchor authority path: {exc}",
                        state=ReviewOperationState.TERMINAL,
                        response_class=ReviewResponseClass.CLIENT_ERROR,
                    )
                except (TypeError, UnicodeDecodeError, ValueError) as exc:
                    return _review_post_error(
                        head_sha=head_sha,
                        error=f"invalid anchor authority artifact: {exc}",
                        state=ReviewOperationState.TERMINAL,
                        response_class=ReviewResponseClass.CLIENT_ERROR,
                    )
                if (
                    authority.repository != repository.casefold()
                    or authority.pr_number != pr_number
                    or authority.head_sha != head_sha
                ):
                    return _review_post_error(
                        head_sha=head_sha,
                        error="anchor authority identity does not match the review request",
                        state=ReviewOperationState.TERMINAL,
                        response_class=ReviewResponseClass.CLIENT_ERROR,
                    )
            try:
                typed_comments = tuple(map(GitHubReviewComment.from_wire, comments))
            except (KeyError, TypeError, ValueError) as exc:
                return _review_post_error(
                    head_sha=head_sha,
                    error=f"{type(exc).__name__}: {exc}",
                    state=ReviewOperationState.TERMINAL,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                )
            request = GitHubReviewRequest(
                cwd=cwd,
                receipt_path=receipt_path,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                logical_iteration=logical_iteration,
                anchor_authority=authority,
                event=event,
                body=body,
                comments=typed_comments,
                dry_run=dry_run,
            )
            await _notify(
                ctx,
                "info",
                f"post_pr_review: #{pr_number}",
                "autoskillit.post_pr_review",
                extra={"repository": repository, "dry_run": dry_run},
            )
            result = await poster.post(request)
            if (
                result.state
                in {
                    ReviewOperationState.SUCCEEDED,
                    ReviewOperationState.RECONCILED,
                }
                and result.receipt is not None
            ):
                atomic_write(
                    destination,
                    json.dumps(
                        result.receipt.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    strict_durability=True,
                )
                os.chmod(destination, 0o600)
                result = replace(result, receipt_path=destination)
            await _notify(
                ctx,
                "info",
                f"post_pr_review: {result.state}",
                "autoskillit.post_pr_review.complete",
                extra={"operation_key": result.operation_key},
            )
            return json.dumps(result.to_dict())
        except Exception as exc:
            logger.error("post_pr_review unhandled exception", exc_info=True)
            return _review_post_error(
                head_sha=head_sha,
                error=f"{type(exc).__name__}: {exc}",
                state=ReviewOperationState.AMBIGUOUS,
                response_class=ReviewResponseClass.SERVER_ERROR,
            )


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
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
    structlog.contextvars.clear_contextvars()
    with structlog.contextvars.bound_contextvars(tool="bulk_close_issues", cwd=cwd):
        try:
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
