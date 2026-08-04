"""Typed GitHub REST gateway used only by the review state machine."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from autoskillit.core import GitHubApiLog, ReviewResponseClass
from autoskillit.execution._github_http import is_secondary_rate_limit
from autoskillit.execution.github import github_headers, make_tracked_httpx_client


@dataclass(frozen=True, slots=True)
class GatewayResult:
    status_code: int | None
    data: Any
    headers: dict[str, str]
    response_class: ReviewResponseClass
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


@dataclass(frozen=True, slots=True)
class CredentialScopeMaterial:
    """Ephemeral credential/origin material that must never be persisted or logged."""

    credential: str
    api_origin: str


class DefaultGitHubReviewGateway:
    def __init__(
        self,
        *,
        token_factory: Callable[[], str | None],
        api_log: GitHubApiLog | None,
        base_url: str = "https://api.github.com",
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self._token_factory = token_factory
        self._api_log = api_log
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory
        self._token_snapshot: str | None | object = _UNRESOLVED

    async def scope_material(self) -> CredentialScopeMaterial:
        token = await self._token()
        parsed = urlsplit(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GitHub API base URL must have an HTTP(S) origin")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}:{port}"
        return CredentialScopeMaterial(credential=token or "", api_origin=origin)

    async def get_authenticated_user(self) -> GatewayResult:
        return await self._request("GET", "/user")

    async def get_pull(self, repository: str, pr_number: int) -> GatewayResult:
        return await self._request("GET", f"/repos/{repository}/pulls/{pr_number}")

    async def create_review(
        self,
        repository: str,
        pr_number: int,
        payload: dict[str, Any],
    ) -> GatewayResult:
        return await self._request(
            "POST",
            f"/repos/{repository}/pulls/{pr_number}/reviews",
            json=payload,
        )

    async def list_reviews(self, repository: str, pr_number: int) -> GatewayResult:
        return await self._paginate(f"/repos/{repository}/pulls/{pr_number}/reviews?per_page=100")

    async def list_review_comments(
        self,
        repository: str,
        pr_number: int,
        review_id: int,
    ) -> GatewayResult:
        return await self._paginate(
            f"/repos/{repository}/pulls/{pr_number}/reviews/{review_id}/comments?per_page=100"
        )

    async def _paginate(self, path: str) -> GatewayResult:
        items: list[Any] = []
        next_path: str | None = path
        headers: dict[str, str] = {}
        while next_path is not None:
            response = await self._request("GET", next_path)
            headers = response.headers
            if not response.succeeded:
                return response
            if not isinstance(response.data, list):
                return GatewayResult(
                    status_code=response.status_code,
                    data=None,
                    headers=response.headers,
                    response_class=ReviewResponseClass.CLIENT_ERROR,
                    error="GitHub pagination response was not a list",
                )
            items.extend(response.data)
            next_path = self._next_page_path(response.headers.get("link"))
        return GatewayResult(
            status_code=200,
            data=items,
            headers=headers,
            response_class=ReviewResponseClass.SUCCESS,
        )

    def _next_page_path(self, link_header: str | None) -> str | None:
        if not link_header:
            return None
        for part in link_header.split(","):
            url_part, *parameters = part.strip().split(";")
            if not any(parameter.strip() == 'rel="next"' for parameter in parameters):
                continue
            if not (url_part.startswith("<") and url_part.endswith(">")):
                raise ValueError("GitHub pagination Link header is malformed")
            absolute = urljoin(f"{self._base_url}/", url_part[1:-1])
            expected = urlsplit(self._base_url)
            parsed = urlsplit(absolute)
            if (
                parsed.scheme.casefold(),
                parsed.hostname,
                parsed.port,
            ) != (
                expected.scheme.casefold(),
                expected.hostname,
                expected.port,
            ):
                raise ValueError("GitHub pagination escaped the configured API origin")
            return parsed.path + (f"?{parsed.query}" if parsed.query else "")
        return None

    async def _token(self) -> str | None:
        if self._token_snapshot is _UNRESOLVED:
            value = self._token_factory()
            if inspect.isawaitable(value):
                value = await value
            self._token_snapshot = value
        return self._token_snapshot if isinstance(self._token_snapshot, str) else None

    async def _request(self, method: str, path: str, **kwargs: Any) -> GatewayResult:
        try:
            token = await self._token()
            if self._client_factory is None:
                client = make_tracked_httpx_client(
                    self._api_log,
                    timeout=httpx.Timeout(30.0),
                    headers=github_headers(token),
                    base_url=self._base_url,
                )
            else:
                client = self._client_factory(
                    timeout=httpx.Timeout(30.0),
                    headers=github_headers(token),
                    base_url=self._base_url,
                )
            async with client:
                response = await client.request(method, path, **kwargs)
            try:
                data = response.json()
            except ValueError:
                data = None
            headers = {key.lower(): value for key, value in response.headers.items()}
            return GatewayResult(
                status_code=response.status_code,
                data=data,
                headers=headers,
                response_class=_classify_response(
                    response.status_code,
                    data,
                    headers,
                ),
            )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            return GatewayResult(
                status_code=None,
                data=None,
                headers={},
                response_class=ReviewResponseClass.TRANSPORT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )


def _classify_response(
    status_code: int,
    data: Any,
    headers: dict[str, str],
) -> ReviewResponseClass:
    if 200 <= status_code < 300:
        return ReviewResponseClass.SUCCESS
    if status_code >= 500:
        return ReviewResponseClass.SERVER_ERROR
    if is_secondary_rate_limit(
        status_code=status_code,
        data=data,
        headers=headers,
    ):
        return ReviewResponseClass.SECONDARY_RATE_LIMIT
    if status_code == 422:
        return ReviewResponseClass.VALIDATION_ERROR
    return ReviewResponseClass.CLIENT_ERROR


_UNRESOLVED = object()
