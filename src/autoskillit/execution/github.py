"""GitHub issue fetcher.

L1 module: depends only on stdlib, httpx, and core/logging.
Never raises — all errors are captured and returned as {"success": False, "error": "..."}.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from autoskillit.core import _parse_issue_ref, get_logger

if TYPE_CHECKING:
    from autoskillit.core._type_protocols import GitHubApiLog

logger = get_logger(__name__)


def _slugify(title: str) -> str:
    """Convert an issue title to a URL-safe branch prefix slug.

    Lowercases, replaces non-alphanumeric sequences with hyphens,
    strips leading/trailing hyphens, and truncates to 60 chars at a word boundary.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rstrip("-")
    return slug


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


def github_headers(token: str | None) -> dict[str, str]:
    """Build the standard GitHub REST API headers dict.

    Returns a fresh dict containing Accept, X-GitHub-Api-Version, and
    User-Agent on every call. Injects Authorization when token is provided.
    Single source of truth for all three execution-layer GitHub clients.
    """
    h: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "autoskillit",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class _TrackingTransport(httpx.AsyncBaseTransport):
    """httpx transport wrapper that records each request into a GitHubApiLog."""

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        tracker: GitHubApiLog,
    ) -> None:
        self._wrapped = wrapped
        self._tracker = tracker

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        start = time.monotonic()
        response = await self._wrapped.handle_async_request(request)
        latency_ms = (time.monotonic() - start) * 1000.0
        headers = response.headers

        def _int(key: str) -> int:
            try:
                return int(headers.get(key, "-1"))
            except ValueError:
                return -1

        await self._tracker.record_httpx(
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            latency_ms=latency_ms,
            rate_limit_remaining=_int("x-ratelimit-remaining"),
            rate_limit_used=_int("x-ratelimit-used"),
            rate_limit_reset=_int("x-ratelimit-reset"),
            timestamp=datetime.now(UTC).isoformat(),
        )
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def make_tracked_httpx_client(
    tracker: GitHubApiLog | None,
    *,
    timeout: httpx.Timeout,
    headers: dict[str, str] | None = None,
    limits: httpx.Limits | None = None,
    base_url: str | None = None,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient, optionally wrapping transport with tracking."""
    kwargs: dict[str, Any] = {"timeout": timeout}
    if headers is not None:
        kwargs["headers"] = headers
    if limits is not None:
        kwargs["limits"] = limits
    if base_url is not None:
        kwargs["base_url"] = base_url
    if tracker is not None:
        kwargs["transport"] = _TrackingTransport(httpx.AsyncHTTPTransport(), tracker)
    return httpx.AsyncClient(**kwargs)


def parse_merge_queue_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a GitHub GraphQL merge queue response into a sorted entry list.

    Returns a list of {position, state, pr_number, pr_title} dicts sorted by
    position ascending.  Returns an empty list when:
    - the queue is not enabled (mergeQueue is null)
    - the queue has no entries
    - the response contains GraphQL errors (logged at WARNING level)
    - the response structure is unexpected

    A node with a missing ``pullRequest`` key is included with ``pr_number=None``
    rather than skipped, so callers can inspect and handle partial entries.
    """
    if "errors" in data:
        logger.warning("parse_merge_queue_response graphql errors", errors=data["errors"])
        return []
    try:
        queue = data.get("data", {}).get("repository", {}).get("mergeQueue") or {}
        nodes = (queue.get("entries") or {}).get("nodes") or []
    except (AttributeError, TypeError):
        return []
    entries = []
    for node in nodes:
        try:
            pr = node.get("pullRequest") or {}
            entries.append(
                {
                    "position": node.get("position", float("inf")),
                    "state": node.get("state", ""),
                    "pr_number": pr.get("number"),
                    "pr_title": pr.get("title", ""),
                }
            )
        except (AttributeError, TypeError):
            continue
    return sorted(entries, key=lambda e: e["position"])


class DefaultGitHubFetcher:
    """Concrete GitHub issue fetcher using the GitHub REST API via httpx.

    Implements the GitHubFetcher protocol.
    Never raises — errors are returned as {"success": False, "error": "..."}.
    """

    _UNRESOLVED = object()

    def __init__(
        self,
        *,
        token: str | None | Callable[[], str | None] = None,
        tracker: GitHubApiLog | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token_factory: Callable[[], str | None] | None
        if callable(token):
            self._token_factory = token
            self._token: str | None = self._UNRESOLVED  # type: ignore[assignment]
        else:
            self._token_factory = None
            self._token = token
        self._tracker = tracker
        self._base_url = base_url.rstrip("/")
        self._last_mutating_ts: float = 0.0
        self._mutating_lock: asyncio.Lock = asyncio.Lock()
        self._label_cache: set[tuple[str, str, str]] = set()

    def _resolve_token(self) -> str | None:
        if self._token is self._UNRESOLVED:
            self._token = self._token_factory() if self._token_factory is not None else None
        return self._token

    @property
    def has_token(self) -> bool:
        """True if this fetcher has an authentication token (resolves lazily)."""
        return bool(self._resolve_token())

    def _headers(self) -> dict[str, str]:
        return github_headers(self._resolve_token())

    async def _throttle_mutating(self) -> None:
        """Enforce >= 1s gap between mutating GitHub API calls.

        Acquires an async lock to serialize concurrent mutating calls,
        then sleeps for the remaining time if < 1s has elapsed since
        the last mutating call.
        """
        async with self._mutating_lock:
            now = time.monotonic()
            elapsed = now - self._last_mutating_ts
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
            self._last_mutating_ts = time.monotonic()

    async def fetch_issue(
        self,
        issue_ref_or_owner: str,
        repo: str | None = None,
        number: int | None = None,
        *,
        include_comments: bool = True,
    ) -> dict[str, Any]:
        """Fetch a GitHub issue. Returns structured data + Markdown content.

        Accepts either a single issue_ref string ("owner/repo#N") or separate
        (owner, repo, number) positional arguments. Never raises.
        """
        if repo is not None and number is not None:
            owner = issue_ref_or_owner
            issue_ref = f"{owner}/{repo}#{number}"
        else:
            try:
                owner, repo, number = _parse_issue_ref(issue_ref_or_owner)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            issue_ref = issue_ref_or_owner

        path = f"/repos/{owner}/{repo}/issues/{number}"

        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.get(path)
                if resp.status_code == 404:
                    if not self.has_token:
                        return {
                            "success": False,
                            "error": (
                                f"Issue not found (HTTP 404): {issue_ref}. "
                                "The repository may be private or restricted. "
                                "Set github.token in .autoskillit/.secrets.yaml, "
                                "set GITHUB_TOKEN, or log in with gh auth login."
                            ),
                        }
                    return {"success": False, "error": f"Issue not found: {issue_ref}"}
                if resp.status_code == 401:
                    return {"success": False, "error": "GitHub authentication failed (401)"}
                resp.raise_for_status()
                issue_data = resp.json()

                comments: list[dict[str, str]] = []
                if include_comments and issue_data.get("comments", 0) > 0:
                    c_resp = await client.get(f"{path}/comments", params={"per_page": 100})
                    c_resp.raise_for_status()
                    comments = [
                        {"author": c["user"]["login"], "body": c["body"] or ""}
                        for c in c_resp.json()
                    ]

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github fetch http error",
                status=exc.response.status_code,
                ref=issue_ref,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github fetch request error", ref=issue_ref, error=str(exc))
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
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.get(
                    "/search/issues",
                    params={"q": search_query, "per_page": 5},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github search http error", status=exc.response.status_code, query=query
            )
            return {"success": False, "error": f"HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            logger.warning("github search request error", query=query, error=str(exc))
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
        await self._throttle_mutating()
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.post(
                    f"/repos/{owner}/{repo}/issues",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("github create_issue http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github create_issue request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}

        return {
            "success": True,
            "issue_number": data["number"],
            "url": data.get("html_url", ""),
        }

    async def update_issue_body(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        new_body: str,
    ) -> dict[str, Any]:
        """PATCH an existing issue's body field.

        Returns {success, issue_url}. Never raises.
        """
        await self._throttle_mutating()
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.patch(
                    f"/repos/{owner}/{repo}/issues/{issue_number}",
                    json={"body": new_body},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github update_issue_body http error",
                status=exc.response.status_code,
                issue=issue_number,
                exc_info=True,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning(
                "github update_issue_body request error", issue=issue_number, error=str(exc)
            )
            return {"success": False, "error": f"Request error: {exc}"}

        return {"success": True, "issue_url": data.get("html_url", "")}

    async def fetch_title(self, issue_url: str) -> dict[str, object]:
        """Fetch only the title and slug for a GitHub issue — no body, no comments.

        Returns {success, number, title, slug}. Never raises.
        Makes exactly one HTTP call regardless of issue comment count.
        """
        try:
            owner, repo, number = _parse_issue_ref(issue_url)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        path = f"/repos/{owner}/{repo}/issues/{number}"
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.get(path)
                if resp.status_code == 404:
                    hint = (
                        " Set github.token in .autoskillit/.secrets.yaml,"
                        " set GITHUB_TOKEN, or log in with gh auth login."
                        if not self.has_token
                        else ""
                    )
                    return {
                        "success": False,
                        "error": f"Issue {owner}/{repo}#{number} not found.{hint}",
                    }
                if resp.status_code == 401:
                    return {"success": False, "error": "GitHub authentication failed (401)"}
                resp.raise_for_status()
                data = resp.json()
                title: str = data.get("title", "")
                slug = _slugify(title)
                return {"success": True, "number": data["number"], "title": title, "slug": slug}
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            return {"success": False, "error": f"Request error: {exc}"}

    async def add_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> dict[str, Any]:
        """Add labels to an issue. Returns {success, labels}. Never raises."""
        await self._throttle_mutating()
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.post(
                    f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
                    json={"labels": labels},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("github add_labels http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github add_labels request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}
        applied = [lbl["name"] for lbl in data]
        return {"success": True, "labels": applied}

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> dict[str, Any]:
        """Remove a label from an issue. 404 is treated as success (idempotent).
        Returns {success}. Never raises."""
        await self._throttle_mutating()
        encoded = label.replace(" ", "%20")
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.delete(
                    f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{encoded}",
                )
                if resp.status_code == 404:
                    return {"success": True}  # already removed — idempotent
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("github remove_label http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github remove_label request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}
        return {"success": True}

    async def swap_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        remove_labels: list[str],
        add_labels: list[str],
    ) -> dict[str, Any]:
        """Swap labels on an issue via GET-then-PUT (reduced-race replacement).

        Fetches the current label set, computes (current - remove_labels) | add_labels,
        then PUTs the result. The PUT endpoint replaces all labels in one HTTP call,
        reducing the race window compared to DELETE→POST→POST, but is not truly atomic:
        a concurrent writer between the GET (fetch) and PUT (replace) can silently
        overwrite interleaved changes.

        Returns {success, labels}. Never raises.
        """
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                # Step 1: GET current labels (read-only, no throttle needed)
                get_resp = await client.get(
                    f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
                )
                get_resp.raise_for_status()
                current = {lbl["name"] for lbl in get_resp.json()}

                # Step 2: Compute target set
                remove_set = set(remove_labels)
                add_set = set(add_labels)
                target = sorted((current - remove_set) | add_set)

                # Step 3: PUT atomically (throttle the single mutating call)
                await self._throttle_mutating()
                put_resp = await client.put(
                    f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
                    json={"labels": target},
                )
                put_resp.raise_for_status()
                applied = [lbl["name"] for lbl in put_resp.json()]
        except httpx.HTTPStatusError as exc:
            logger.warning("github swap_labels http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github swap_labels request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}
        return {"success": True, "labels": applied}

    async def ensure_label(
        self,
        owner: str,
        repo: str,
        label: str,
        color: str = "ededed",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a label if it doesn't exist. 422 (already exists) is success.
        Returns {success, created}. Never raises.
        Caches successful results per (owner, repo, label) triple to skip
        redundant API calls within a session.
        """
        cache_key = (owner, repo, label)
        if cache_key in self._label_cache:
            return {"success": True, "created": False}

        await self._throttle_mutating()
        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
                base_url=self._base_url,
            ) as client:
                resp = await client.post(
                    f"/repos/{owner}/{repo}/labels",
                    json={"name": label, "color": color, "description": description},
                )
                if resp.status_code == 422:
                    self._label_cache.add(cache_key)
                    return {"success": True, "created": False}
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("github ensure_label http error", status=exc.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("github ensure_label request error", error=str(exc))
            return {"success": False, "error": f"Request error: {exc}"}
        self._label_cache.add(cache_key)
        return {"success": True, "created": True}
