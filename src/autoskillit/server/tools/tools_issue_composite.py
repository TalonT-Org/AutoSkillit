"""MCP tool handlers: claim_and_resolve_issue (composite)."""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from autoskillit.core import _parse_issue_ref, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._notify import track_response_size

logger = get_logger(__name__)


def _extract_label_names(raw_labels: list[Any]) -> list[str]:
    return [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in raw_labels if lbl]


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("claim_and_resolve_issue")
async def claim_and_resolve_issue(
    issue_url: str,
    label: str | None = None,
    allow_reentry: bool = False,
) -> str:
    """Fetch the issue title and claim it with an in-progress label in one turn.

    Combines get_issue_title + claim_issue into a single orchestrator call.

    Returns JSON with: success, claimed (bool), issue_number, issue_title,
    issue_slug, timings. When claimed=false, issue_number/title/slug are still
    returned so the orchestrator can use them in logging or abort paths.
    When allow_reentry=True and the label is already present, returns
    claimed=True with reentry=True.

    Args:
        issue_url: Full GitHub issue URL or shorthand (owner/repo#42).
        label: Label name to apply. Defaults to github.in_progress_label from config.
        allow_reentry: When True and label already present, returns claimed=True.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="claim_and_resolve_issue", issue_url=issue_url)
        logger.info("claim_and_resolve_issue", issue_url=issue_url)

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

        _fetch_title_start = time.monotonic()
        title_result = await tool_ctx.github_client.fetch_title(issue_url)
        fetch_title_ms = int((time.monotonic() - _fetch_title_start) * 1000)

        if not title_result.get("success"):
            return json.dumps(
                {"success": False, "error": title_result.get("error", "fetch_title failed")}
            )

        issue_title = title_result.get("title", "")
        issue_slug = title_result.get("slug", "")

        _claim_start = time.monotonic()
        fetch_result = await tool_ctx.github_client.fetch_issue(issue_url, include_comments=False)
        if not fetch_result.get("success"):
            claim_ms = int((time.monotonic() - _claim_start) * 1000)
            return json.dumps(
                {
                    "success": False,
                    "error": fetch_result.get("error", "fetch_issue failed"),
                    "issue_number": issue_number,
                    "issue_title": issue_title,
                    "issue_slug": issue_slug,
                    "timings": {"fetch_title_ms": fetch_title_ms, "claim_ms": claim_ms},
                }
            )

        current_labels = _extract_label_names(fetch_result.get("labels", []))
        if effective_label in current_labels:
            claim_ms = int((time.monotonic() - _claim_start) * 1000)
            if allow_reentry:
                return json.dumps(
                    {
                        "success": True,
                        "claimed": True,
                        "reentry": True,
                        "issue_number": issue_number,
                        "issue_title": issue_title,
                        "issue_slug": issue_slug,
                        "label": effective_label,
                        "timings": {"fetch_title_ms": fetch_title_ms, "claim_ms": claim_ms},
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
                    "issue_number": issue_number,
                    "issue_title": issue_title,
                    "issue_slug": issue_slug,
                    "timings": {"fetch_title_ms": fetch_title_ms, "claim_ms": claim_ms},
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
        claim_ms = int((time.monotonic() - _claim_start) * 1000)

        if not swap_result.get("success"):
            return json.dumps(
                {
                    "success": False,
                    "error": swap_result.get("error", "swap_labels failed"),
                    "issue_number": issue_number,
                    "issue_title": issue_title,
                    "issue_slug": issue_slug,
                    "timings": {"fetch_title_ms": fetch_title_ms, "claim_ms": claim_ms},
                }
            )

        return json.dumps(
            {
                "success": True,
                "claimed": True,
                "issue_number": issue_number,
                "issue_title": issue_title,
                "issue_slug": issue_slug,
                "label": effective_label,
                "timings": {"fetch_title_ms": fetch_title_ms, "claim_ms": claim_ms},
            }
        )
    except Exception as exc:
        logger.error("claim_and_resolve_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
