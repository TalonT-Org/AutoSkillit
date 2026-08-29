"""Tests for shared architectural-deferral registry checks."""

from datetime import date

import pytest

from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _entry(issue: int = 1234) -> TrackedDeferral:
    return TrackedDeferral(
        issue=issue,
        rationale="A concrete deferred architectural violation remains live.",
        added_date=date.today(),
        regression_test="tests/arch/test_deferred_debt.py::test_every_entry_present_passes",
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


def test_deferral_without_regression_test_is_rejected() -> None:
    entry = _entry()
    entry = TrackedDeferral(
        issue=entry.issue,
        rationale=entry.rationale,
        added_date=entry.added_date,
        regression_test="",
    )

    with pytest.raises(AssertionError, match="TEST_REGISTRY"):
        assert_deferrals_have_regression_tests(
            {"missing-evidence": entry},
            registry_name="TEST_REGISTRY",
            collected_node_ids=set(),
        )


@pytest.mark.parametrize(
    "stale_node_id",
    [
        "tests/arch/test_deferred_debt.py::test_deleted_regression",
        "tests/arch/test_deferred_debt.py::test_renamed_regression",
        "tests/arch/test_deferred_debt.py::test_parametrized_regression[old-case]",
    ],
)
def test_deferral_with_orphaned_regression_test_is_rejected(stale_node_id: str) -> None:
    with pytest.raises(AssertionError) as exc_info:
        assert_deferrals_have_regression_tests(
            {
                "missing-evidence": TrackedDeferral(
                    issue=1234,
                    rationale="A concrete deferred architectural violation remains live.",
                    added_date=date.today(),
                    regression_test=stale_node_id,
                )
            },
            registry_name="TEST_REGISTRY",
            collected_node_ids={
                "tests/arch/test_deferred_debt.py::test_current_regression",
                "tests/arch/test_deferred_debt.py::test_parametrized_regression[current-case]",
            },
        )
    assert "missing-evidence" in str(exc_info.value)
    assert stale_node_id in str(exc_info.value)
