"""GitHub issue fetcher.

L1 module: depends only on stdlib, httpx, and core/logging.
Never raises — all errors are captured and returned as {"success": False, "error": "..."}.
"""

from __future__ import annotations

from typing import Any

import httpx

from autoskillit.core import _parse_issue_ref, get_logger

_log = get_logger(__name__)

# Re-export for callers that import _parse_issue_ref from this module.
__all__ = ["_parse_issue_ref"]


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

    @property
    def has_token(self) -> bool:
        """True if this fetcher was constructed with an authentication token."""
        return bool(self._token)

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
                    if not self.has_token:
                        return {
                            "success": False,
                            "error": (
                                f"Issue not found (HTTP 404): {issue_ref}. "
                                "The repository may be private or restricted. "
                                "Configure github.token in .autoskillit/config.yaml "
                                "or set GITHUB_TOKEN before starting the server."
                            ),
                        }
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

    async def search_issues(
        self,
        query: str,
        owner: str,
        repo: str,
        *,
        state: str = "open",
    ) -> dict[str, Any]:
        """Search for issues in a repo matching query text.

        Returns {success, total_count, items: [{number, title, html_url, body, state}]}.
        Never raises.
        """
        search_query = f'repo:{owner}/{repo} is:issue state:{state} "{query}"'
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.get(
                    "https://api.github.com/search/issues",
                    headers=headers,
                    params={"q": search_query, "per_page": 5},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning("github search http error", status=exc.response.status_code, query=query)
            return {"success": False, "error": f"HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            _log.warning("github search request error", query=query, error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        items = [
            {
                "number": item["number"],
                "title": item.get("title", ""),
                "html_url": item.get("html_url", ""),
                "body": item.get("body") or "",
                "state": item.get("state", ""),
            }
            for item in data.get("items", [])
        ]
        return {"success": True, "total_count": data.get("total_count", 0), "items": items}

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue in the repo.

        Returns {success, issue_number, url}. Never raises.
        """
        headers = self._headers()
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning("github create_issue http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("github create_issue request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        return {
            "success": True,
            "issue_number": data["number"],
            "url": data.get("html_url", ""),
        }

    async def add_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a comment on an existing issue.

        Returns {success, comment_id, url}. Never raises.
        """
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
                    headers=headers,
                    json={"body": body},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "github add_comment http error",
                status=exc.response.status_code,
                issue=issue_number,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("github add_comment request error", issue=issue_number, error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        return {
            "success": True,
            "comment_id": data.get("id"),
            "url": data.get("html_url", ""),
        }

    async def add_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> dict[str, Any]:
        """Add labels to an issue.

        Returns {success, labels: [names]}. Never raises.
        """
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/labels",
                    headers=headers,
                    json={"labels": labels},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "github add_labels http error",
                status=exc.response.status_code,
                issue=issue_number,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("github add_labels request error", issue=issue_number, error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        return {
            "success": True,
            "labels": [lbl["name"] for lbl in data if isinstance(lbl, dict)],
        }

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> dict[str, Any]:
        """Remove a label from an issue. Idempotent — 404 is treated as success.

        Returns {success}. Never raises.
        """
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.delete(
                    f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
                    headers=headers,
                )
                if resp.status_code == 404:
                    return {"success": True}
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "github remove_label http error",
                status=exc.response.status_code,
                issue=issue_number,
                label=label,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning(
                "github remove_label request error",
                issue=issue_number,
                label=label,
                error=str(exc),
            )
            return {"success": False, "error": f"Request error: {exc}"}

        return {"success": True}

    async def ensure_label(
        self,
        owner: str,
        repo: str,
        label: str,
        color: str = "fbca04",
        description: str = "",
    ) -> dict[str, Any]:
        """Ensure a label exists in the repo. Idempotent — 422 (already exists) is success.

        Returns {success, created}. Never raises.
        """
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/labels",
                    headers=headers,
                    json={"name": label, "color": color, "description": description},
                )
                if resp.status_code == 422:
                    return {"success": True, "created": False}
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "github ensure_label http error",
                status=exc.response.status_code,
                label=label,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("github ensure_label request error", label=label, error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        return {"success": True, "created": True}
