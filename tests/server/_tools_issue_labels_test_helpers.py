"""Shared helpers for tools_issue_labels server tests."""

from __future__ import annotations

from collections.abc import Sequence

from tests.fakes import FakeGitHubFetcher

_ISSUE_KEY: tuple[str, str, int] = ("owner", "repo", 42)


def _coerce_labels(labels: Sequence[str] | None, *, default: list[str]) -> list[str]:
    if labels is None:
        return list(default)
    if isinstance(labels, str):
        raise TypeError(
            "labels must be a Sequence[str], got str (use a list to pass multiple labels)"
        )
    return list(labels)


def make_release_issue_fake(
    labels: Sequence[str] | None = None,
    *,
    body: str = "",
    state: str = "open",
    repository_labels: Sequence[str] | None = None,
) -> FakeGitHubFetcher:
    """Build a :class:`FakeGitHubFetcher` seeded with the canonical issue key.

    ``labels`` defaults to ``["in-progress"]`` to match the post-claim state
    expected by most release_issue scenarios.
    """
    issue_labels = _coerce_labels(labels, default=["in-progress"])
    repo_labels = _coerce_labels(repository_labels, default=[])
    return FakeGitHubFetcher(
        issues={_ISSUE_KEY: {"labels": issue_labels, "body": body, "state": state}},
        repository_labels={("owner", "repo"): repo_labels},
    )


def calls_for(
    fake: FakeGitHubFetcher, operation: str
) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    """Filter ``fake.call_log`` to entries for a single operation."""
    return [entry for entry in fake.call_log if entry[0] == operation]
