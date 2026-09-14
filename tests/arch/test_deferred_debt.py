"""Tests for shared architectural-deferral registry checks."""

import ast
from datetime import date

import pytest

from tests.arch._deferred_debt import (
    TrackedDeferral,
    _definition_paths,
    _regression_test_resolves,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
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


def test_stale_entry_is_rejected() -> None:
    entry = TrackedDeferral(
        issue=1234,
        rationale="A concrete deferred architectural violation remains live.",
        added_date=date(2020, 1, 1),
        regression_test="tests/arch/test_deferred_debt.py::test_every_entry_present_passes",
    )
    with pytest.raises(AssertionError, match="TEST_REGISTRY"):
        assert_not_stale({"stale-key": entry}, registry_name="TEST_REGISTRY")


def test_rationale_too_short_is_rejected() -> None:
    entry = TrackedDeferral(
        issue=1234,
        rationale="too short",
        added_date=date.today(),
        regression_test="tests/arch/test_deferred_debt.py::test_every_entry_present_passes",
    )
    with pytest.raises(AssertionError, match="TEST_REGISTRY"):
        assert_rationale_present({"vague-key": entry}, registry_name="TEST_REGISTRY")


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
        )
    assert "missing-evidence" in str(exc_info.value)
    assert stale_node_id in str(exc_info.value)


def test_regression_test_naming_a_live_test_resolves() -> None:
    """Resolution is by source, so a named test resolves whether or not the
    running session collected it — the property that lets these registries
    survive conservative path filtering and CI sharding."""
    assert_deferrals_have_regression_tests(
        {"tracked": _entry()},
        registry_name="TEST_REGISTRY",
    )


def test_regression_test_in_an_unknown_file_is_rejected() -> None:
    entry = TrackedDeferral(
        issue=1234,
        rationale="A concrete deferred architectural violation remains live.",
        added_date=date.today(),
        regression_test="tests/arch/test_no_such_module.py::test_missing",
    )
    with pytest.raises(AssertionError, match="TEST_REGISTRY"):
        assert_deferrals_have_regression_tests(
            {"missing-evidence": entry},
            registry_name="TEST_REGISTRY",
        )


def test_definition_paths_reach_class_nested_methods() -> None:
    tree = ast.parse(
        "def test_top() -> None: ...\nclass TestOuter:\n    def test_inner(self) -> None: ...\n"
    )
    assert _definition_paths(tree.body) == {
        ("test_top",),
        ("TestOuter",),
        ("TestOuter", "test_inner"),
    }


def test_parametrisation_id_resolves_to_its_base_function() -> None:
    """A parametrised node id names a function that exists in source; the bracket
    suffix is generated at collection time, so only the base name is checked."""
    assert _regression_test_resolves(
        "tests/arch/test_deferred_debt.py::test_parametrisation_id_resolves_to_its_base_function[x]"
    )
