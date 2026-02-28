"""GitHub issue fetcher.

L1 module: depends only on stdlib, httpx, and core/logging.
Never raises — all errors are captured and returned as {"success": False, "error": "..."}.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from autoskillit.core import get_logger

_log = get_logger(__name__)

_FULL_URL_RE = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)"
)
_SHORTHAND_RE = re.compile(r"^([^/]+)/([^#]+)#(\d+)$")


def _parse_issue_ref(issue_ref: str) -> tuple[str, str, int]:
    """Parse owner, repo, number from a GitHub issue reference.

    Accepts:
    - Full URL: https://github.com/owner/repo/issues/42
    - Shorthand: owner/repo#42

    Raises ValueError for unrecognised formats (including bare numbers).
    Bare number resolution is the caller's responsibility.
    """
    m = _FULL_URL_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    m = _SHORTHAND_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    raise ValueError(
        f"Cannot parse GitHub issue reference: {issue_ref!r}. "
        "Expected a full URL (https://github.com/owner/repo/issues/N) "
        "or shorthand (owner/repo#N)."
    )


def _format_issue_markdown(
    number: int,
    title: str,
    url: str,
    state: str,
    labels: list[str],
    body: str,
    comments: list[dict[str, str]],
    include_comments: bool,
    is_pull_request: bool = False,
) -> str:
    """Render an issue as Markdown suitable for downstream skill prompts."""
    label_str = ", ".join(labels) if labels else "(none)"
    kind = "Pull Request" if is_pull_request else "Issue"
    header = f"# {title} ({kind})" if is_pull_request else f"# {title}"
    lines: list[str] = [
        header,
        "",
        f"**Issue:** #{number}",
        f"**State:** {state}",
        f"**Labels:** {label_str}",
        f"**URL:** {url}",
        "",
        body or "",
    ]
    if include_comments and comments:
        lines += ["", "## Comments", ""]
        for c in comments:
            lines += [f"**{c['author']}:**", "", c["body"], ""]
    return "\n".join(lines)


class DefaultGitHubFetcher:
    """Concrete GitHub issue fetcher using the GitHub REST API via httpx.

    Implements the GitHubFetcher protocol.
    Never raises — errors are returned as {"success": False, "error": "..."}.
    """

    def __init__(self, *, token: str | None = None) -> None:
        self._token = token

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autoskillit",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def fetch_issue(
        self,
        issue_ref: str,
        *,
        include_comments: bool = True,
    ) -> dict[str, Any]:
        """Fetch a GitHub issue. Returns structured data + Markdown content.

        Never raises.
        """
        try:
            owner, repo, number = _parse_issue_ref(issue_ref)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        headers = self._headers()
        base = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.get(base, headers=headers)
                if resp.status_code == 404:
                    return {"success": False, "error": f"Issue not found: {issue_ref}"}
                if resp.status_code == 401:
                    return {"success": False, "error": "GitHub authentication failed (401)"}
                resp.raise_for_status()
                issue_data = resp.json()

                comments: list[dict[str, str]] = []
                if include_comments and issue_data.get("comments", 0) > 0:
                    c_resp = await client.get(
                        f"{base}/comments", headers=headers, params={"per_page": 100}
                    )
                    c_resp.raise_for_status()
                    comments = [
                        {"author": c["user"]["login"], "body": c["body"] or ""}
                        for c in c_resp.json()
                    ]

        except httpx.HTTPStatusError as exc:
            _log.warning(
                "github fetch http error",
                status=exc.response.status_code,
                ref=issue_ref,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("github fetch request error", ref=issue_ref, error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        labels = [lbl["name"] for lbl in issue_data.get("labels", [])]
        title = issue_data.get("title", "")
        url = issue_data.get("html_url", "")
        state = issue_data.get("state", "")
        body = issue_data.get("body") or ""
        is_pull_request = "pull_request" in issue_data

        content = _format_issue_markdown(
            number=issue_data["number"],
            title=title,
            url=url,
            state=state,
            labels=labels,
            body=body,
            comments=comments,
            include_comments=include_comments,
            is_pull_request=is_pull_request,
        )

        return {
            "success": True,
            "issue_number": issue_data["number"],
            "title": title,
            "url": url,
            "state": state,
            "labels": labels,
            "is_pull_request": is_pull_request,
            "content": content,
        }
