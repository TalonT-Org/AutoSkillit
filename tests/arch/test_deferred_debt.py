"""Tests for shared architectural-deferral registry checks."""

from datetime import date

import pytest

from tests.arch._deferred_debt import TrackedDeferral, assert_entries_still_apply

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _entry(issue: int = 1234) -> TrackedDeferral:
    return TrackedDeferral(
        issue=issue,
        rationale="A concrete deferred architectural violation remains live.",
        added_date=date.today(),
    )


def test_entry_absent_from_live_keys_is_reported_stale() -> None:
    with pytest.raises(AssertionError, match=r"missing-key.*#1234"):
        assert_entries_still_apply(
            {"missing-key": _entry()},
            registry_name="TEST_REGISTRY",
            live_keys={"different-key"},
        )


def test_every_entry_present_passes() -> None:
    assert_entries_still_apply(
        {"live-key": _entry()},
        registry_name="TEST_REGISTRY",
        live_keys={"live-key"},
    )


def test_message_names_the_registry() -> None:
    with pytest.raises(AssertionError, match="TEST_REGISTRY"):
        assert_entries_still_apply(
            {"missing-key": _entry()},
            registry_name="TEST_REGISTRY",
            live_keys=set(),
        )


def test_live_key_absent_from_registry_is_not_this_helpers_concern() -> None:
    assert_entries_still_apply(
        {},
        registry_name="TEST_REGISTRY",
        live_keys={"unexpected-live-key"},
    )
