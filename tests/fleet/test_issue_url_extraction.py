"""Unit tests for ``extract_issue_urls()`` dual-key canonical accessor.

Validates the single canonical function that resolves the singular/plural
ingredient-key mismatch that orphaned labels for the 7th time (issue #4112).
"""

from __future__ import annotations

import pytest

from autoskillit.fleet._issue_url_helpers import extract_issue_urls

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_plural_key_only() -> None:
    """Batch recipes use the plural ``issue_urls`` key."""
    assert extract_issue_urls({"issue_urls": "url1,url2"}) == "url1,url2"


def test_singular_key_only() -> None:
    """Single-issue recipes use the singular ``issue_url`` key."""
    assert extract_issue_urls({"issue_url": "url1"}) == "url1"


def test_plural_wins_when_both_present() -> None:
    """Plural takes precedence when both keys are populated."""
    assert extract_issue_urls({"issue_urls": "url1", "issue_url": "url2"}) == "url1"


def test_empty_dict() -> None:
    """Empty ingredients dict returns empty string."""
    assert extract_issue_urls({}) == ""


def test_none_ingredients() -> None:
    """``None`` ingredients returns empty string (defensive guard)."""
    assert extract_issue_urls(None) == ""


def test_empty_plural_falls_through_to_singular() -> None:
    """Empty plural value falls through to singular lookup."""
    assert extract_issue_urls({"issue_urls": "", "issue_url": "url1"}) == "url1"


def test_unrelated_keys_ignored() -> None:
    """Other ingredient keys do not affect extraction."""
    assert extract_issue_urls({"label": "in-progress", "issue_url": "url1"}) == "url1"


def test_both_empty_returns_empty() -> None:
    """When both keys are empty, return empty string."""
    assert extract_issue_urls({"issue_urls": "", "issue_url": ""}) == ""
