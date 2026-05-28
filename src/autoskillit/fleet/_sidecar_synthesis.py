"""Sidecar-based result synthesis for fleet dispatch outcome."""

from __future__ import annotations

from autoskillit.fleet.result_parser import L3ParseResult
from autoskillit.fleet.sidecar import IssueSidecarEntry


def synthesize_from_sidecar(
    parsed: L3ParseResult,
    sidecar_entries: list[IssueSidecarEntry],
    dispatched_issue_count: int,
) -> L3ParseResult:
    if parsed.outcome != "no_sentinel":
        return parsed
    if not sidecar_entries:
        return parsed
    completed = [e for e in sidecar_entries if e.status == "completed"]
    if len(completed) != dispatched_issue_count:
        return parsed
    if not any(e.pr_url for e in completed):
        return parsed
    pr_urls = [e.pr_url for e in completed if e.pr_url]
    return L3ParseResult(
        outcome="completed_clean",
        payload={
            "success": True,
            "reason": "sidecar_recovery",
            "pr_urls": pr_urls,
            "issue_count": len(completed),
        },
        raw_body=None,
        parse_error=None,
        source="sidecar",
    )
