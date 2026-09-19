"""Shared helpers for tools_issue_labels server tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from tests.fakes import FakeGitHubFetcher

_ISSUE_KEY: tuple[str, str, int] = ("owner", "repo", 42)


def make_release_issue_fake(
    labels: Sequence[str] | None = None,
    *,
    body: str = "",
    state: str = "open",
    repository_labels: Sequence[str] | None = None,
) -> FakeGitHubFetcher:
    """Build a :class:`FakeGitHubFetcher` seeded with the canonical issue key.

    ``labels`` defaults to ``["in-progress"]`` to match the post-claim state
    expected by most release_issue scenarios; pass an explicit list (or rely
    on the validator accepting empty sequences) to override.
    """
    issue_labels = list(labels) if labels is not None else ["in-progress"]
    return FakeGitHubFetcher(
        issues={_ISSUE_KEY: {"labels": issue_labels, "body": body, "state": state}},
        repository_labels={("owner", "repo"): list(repository_labels or [])},
    )


def calls_for(
    fake: FakeGitHubFetcher, operation: str
) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    """Filter ``fake.call_log`` to entries for a single operation."""
    return [
        cast(tuple[str, tuple[object, ...], dict[str, object]], entry)
        for entry in fake.call_log
        if entry[0] == operation
    ]
