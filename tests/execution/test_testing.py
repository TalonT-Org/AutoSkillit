"""L1 unit tests for execution/testing.py — pytest output parsing."""

from __future__ import annotations

import pytest

from autoskillit.execution.testing import parse_pytest_summary as _parse_pytest_summary


class TestParsePytestSummary:
    """_parse_pytest_summary extracts structured counts from pytest output."""

    def test_simple_pass(self):
        assert _parse_pytest_summary("= 100 passed =\n") == {"passed": 100}

    def test_failed_and_passed(self):
        assert _parse_pytest_summary("= 3 failed, 97 passed =\n") == {
            "failed": 3,
            "passed": 97,
        }

    def test_xfailed_parsed_separately(self):
        counts = _parse_pytest_summary("= 8552 passed, 3 xfailed =\n")
        assert counts == {"passed": 8552, "xfailed": 3}
        assert "failed" not in counts

    def test_mixed_all_outcomes(self):
        counts = _parse_pytest_summary(
            "= 1 failed, 2 xfailed, 1 xpassed, 3 skipped, 93 passed =\n"
        )
        assert counts["failed"] == 1
        assert counts["xfailed"] == 2
        assert counts["xpassed"] == 1
        assert counts["skipped"] == 3
        assert counts["passed"] == 93

    def test_error_outcome(self):
        assert _parse_pytest_summary("= 1 error, 99 passed =\n") == {
            "error": 1,
            "passed": 99,
        }

    def test_multiline_finds_summary(self):
        output = "some log output\nERROR in setup\n=== 100 passed in 2.5s ===\n"
        counts = _parse_pytest_summary(output)
        assert counts == {"passed": 100}

    def test_empty_output(self):
        assert _parse_pytest_summary("") == {}

    def test_no_summary_line(self):
        assert _parse_pytest_summary("no test results here\n") == {}

    def test_bare_q_format_failed_and_passed(self):
        """Bare -q format parses correctly — no = delimiters needed."""
        counts = _parse_pytest_summary("3 failed, 97 passed in 2.31s")
        assert counts["failed"] == 3
        assert counts["passed"] == 97

    def test_bare_q_format_passed_only(self):
        """Bare -q single-outcome line."""
        counts = _parse_pytest_summary("100 passed in 1.50s")
        assert counts == {"passed": 100}


class TestParsePytestSummaryAnchored:
    """_parse_pytest_summary only matches lines in the === delimited section."""

    def test_pytest_summary_ignores_non_summary_lines(self):
        """Log output with 'N failed' must not be confused with the summary.

        Test output can contain lines like '3 failed connections reestablished'
        which match the outcome pattern. Only the === delimited summary line
        should be matched.
        """
        stdout = (
            "test_network.py::test_reconnect PASSED\n"
            "3 failed connections reestablished\n"
            "1 error in config reloaded successfully\n"
            "=== 5 passed in 2.1s ===\n"
        )
        counts = _parse_pytest_summary(stdout)
        assert counts == {"passed": 5}
        assert "failed" not in counts
        assert "error" not in counts


_REALISTIC_VERBOSE = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.12\n"
    "collected 100 items\n"
    "\n"
    "tests/test_foo.py::test_a PASSED                                         [  1%]\n"
    "tests/test_foo.py::test_b FAILED                                         [  2%]\n"
    "...\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_foo.py::test_b - AssertionError\n"
    "========================== 2 failed, 98 passed in 3.45s ==========================\n"
)

PYTEST_SUMMARY_FORMAT_VARIANTS = [
    pytest.param("verbose_single_outcome", "= 5 passed in 1.23s =", {"passed": 5}),
    pytest.param(
        "verbose_multiple_outcomes", "= 3 failed, 97 passed in 2.0s =", {"failed": 3, "passed": 97}
    ),
    pytest.param(
        "verbose_with_warnings", "= 100 passed, 5 warnings in 3s =", {"passed": 100, "warning": 5}
    ),
    pytest.param(
        "verbose_with_xfailed", "= 97 passed, 3 xfailed in 1.2s =", {"passed": 97, "xfailed": 3}
    ),
    pytest.param(
        "verbose_with_xpassed", "= 95 passed, 5 xpassed in 2.0s =", {"passed": 95, "xpassed": 5}
    ),
    pytest.param("verbose_with_errors", "= 1 error in 0.48s =", {"error": 1}),
    pytest.param(
        "verbose_with_deselected",
        "= 50 passed, 50 deselected in 1.0s =",
        {"passed": 50, "deselected": 50},
    ),
    pytest.param("bare_q_passed_only", "100 passed in 1.50s", {"passed": 100}),
    pytest.param(
        "bare_q_failed_and_passed", "3 failed, 97 passed in 2.31s", {"failed": 3, "passed": 97}
    ),
    pytest.param(
        "bare_q_with_warnings", "97 passed, 5 warnings in 3.1s", {"passed": 97, "warning": 5}
    ),
    pytest.param(
        "bare_q_multiline",
        "FAILED tests/test_foo.py::test_a - AssertionError\n2 failed, 98 passed in 4.12s\n",
        {"failed": 2, "passed": 98},
    ),
    pytest.param("empty_string", "", {}),
    pytest.param("no_summary_log_only", "collected 100 items\nsome log output\n", {}),
    pytest.param("realistic_verbose_full_output", _REALISTIC_VERBOSE, {"failed": 2, "passed": 98}),
]


@pytest.mark.parametrize("_id,stdout,expected", PYTEST_SUMMARY_FORMAT_VARIANTS)
def test_parse_pytest_summary_format_variants(_id, stdout, expected):
    assert _parse_pytest_summary(stdout) == expected


def test_parse_pytest_summary_returns_empty_dict_for_no_match():
    assert _parse_pytest_summary("no summary here") == {}


def test_parse_pytest_summary_only_matches_equals_delimited_lines():
    assert _parse_pytest_summary("3 failed connections\n= 5 passed =") == {"passed": 5}


def test_check_test_passed_true_on_zero_rc_clean_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 50 passed in 1s =") is True


def test_check_test_passed_false_on_nonzero_rc():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(1, "= 50 passed in 1s =") is False


def test_check_test_passed_false_on_zero_rc_with_failed_in_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 2 failed, 48 passed =") is False


def test_check_test_passed_false_on_zero_rc_with_error_in_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 1 error =") is False


def test_check_test_passed_true_for_xfailed_skipped():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 97 passed, 3 xfailed, 1 skipped =") is True


def test_check_test_passed_false_when_no_summary_empty_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "") is False


def test_check_test_passed_false_when_no_summary_log_only():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "collected 100 items\nsome log output\n") is False


def test_check_test_passed_false_bare_q_failures():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "3 failed, 97 passed in 2.31s") is False


def test_check_test_passed_true_bare_q_clean():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "100 passed in 1.50s") is True
