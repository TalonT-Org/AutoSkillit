"""Unit tests for core.github_url.parse_github_repo."""

from __future__ import annotations

import pytest

from autoskillit.core import parse_github_repo

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize(
    "url,expected",
    [
        # HTTPS with .git
        ("https://github.com/owner/repo.git", "owner/repo"),
        # SSH with .git
        ("git@github.com:owner/repo.git", "owner/repo"),
        # HTTPS without .git
        ("https://github.com/owner/repo", "owner/repo"),
        # file:// URLs must return None — clone isolation contract
        ("file:///home/user/autoskillit-runs/run-20260315/repo", None),
        ("file://localhost/path/to/repo.git", None),
        # Strict two-segment: owner/repo/extra must return None (resolves divergence)
        ("https://github.com/owner/repo/extra", None),
        # Non-GitHub hosts return None
        ("https://gitlab.com/owner/repo.git", None),
        # Non-URL strings
        ("not-a-github-url", None),
        # Empty string
        ("", None),
    ],
)
def test_parse_github_repo(url: str, expected: str | None) -> None:
    assert parse_github_repo(url) == expected


def test_parse_issue_ref_in_github_url():
    from autoskillit.core.github_url import _parse_issue_ref

    owner, repo, num = _parse_issue_ref("https://github.com/acme/proj/issues/42")
    assert owner == "acme" and repo == "proj" and num == 42


def test_parse_issue_ref_shorthand_in_github_url():
    from autoskillit.core.github_url import _parse_issue_ref

    owner, repo, num = _parse_issue_ref("acme/proj#7")
    assert owner == "acme" and repo == "proj" and num == 7


def test_parse_issue_ref_still_importable_via_core_gateway():
    from autoskillit.core import _parse_issue_ref

    assert callable(_parse_issue_ref)
