"""Tests for test_runner.py public API."""

import pytest

# ---------------------------------------------------------------------------
# Living spec: all known pytest output format variants
# ---------------------------------------------------------------------------
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
    # id, input_stdout, expected_counts
    pytest.param(
        "verbose_single_outcome",
        "= 5 passed in 1.23s =",
        {"passed": 5},
    ),
    pytest.param(
        "verbose_multiple_outcomes",
        "= 3 failed, 97 passed in 2.0s =",
        {"failed": 3, "passed": 97},
    ),
    pytest.param(
        "verbose_with_warnings",
        "= 100 passed, 5 warnings in 3s =",
        {"passed": 100, "warning": 5},
    ),
    pytest.param(
        "verbose_with_xfailed",
        "= 97 passed, 3 xfailed in 1.2s =",
        {"passed": 97, "xfailed": 3},
    ),
    pytest.param(
        "verbose_with_xpassed",
        "= 95 passed, 5 xpassed in 2.0s =",
        {"passed": 95, "xpassed": 5},
    ),
    pytest.param(
        "verbose_with_errors",
        "= 1 error in 0.48s =",
        {"error": 1},
    ),
    pytest.param(
        "verbose_with_deselected",
        "= 50 passed, 50 deselected in 1.0s =",
        {"passed": 50, "deselected": 50},
    ),
    pytest.param(
        "bare_q_passed_only",
        "100 passed in 1.50s",
        {"passed": 100},
    ),
    pytest.param(
        "bare_q_failed_and_passed",
        "3 failed, 97 passed in 2.31s",
        {"failed": 3, "passed": 97},
    ),
    pytest.param(
        "bare_q_with_warnings",
        "97 passed, 5 warnings in 3.1s",
        {"passed": 97, "warning": 5},
    ),
    pytest.param(
        "bare_q_multiline",
        (
            "FAILED tests/test_foo.py::test_a - AssertionError\n"
            "2 failed, 98 passed in 4.12s\n"
        ),
        {"failed": 2, "passed": 98},
    ),
    pytest.param(
        "empty_string",
        "",
        {},
    ),
    pytest.param(
        "no_summary_log_only",
        "collected 100 items\nsome log output\n",
        {},
    ),
    pytest.param(
        "realistic_verbose_full_output",
        _REALISTIC_VERBOSE,
        {"failed": 2, "passed": 98},
    ),
]


@pytest.mark.parametrize("_id,stdout,expected", PYTEST_SUMMARY_FORMAT_VARIANTS)
def test_parse_pytest_summary_format_variants(_id, stdout, expected):
    from autoskillit.execution.testing import parse_pytest_summary

    assert parse_pytest_summary(stdout) == expected


def test_parse_pytest_summary_returns_passed_count():
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("= 42 passed in 1.5s =")
    assert result == {"passed": 42}


def test_parse_pytest_summary_returns_failed_and_passed():
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("= 3 failed, 97 passed in 2.0s =")
    assert result["failed"] == 3
    assert result["passed"] == 97


def test_parse_pytest_summary_returns_empty_dict_for_no_match():
    from autoskillit.execution.testing import parse_pytest_summary

    assert parse_pytest_summary("no summary here") == {}


def test_parse_pytest_summary_only_matches_equals_delimited_lines():
    # Should NOT match plain log lines mentioning "3 failed connections"
    from autoskillit.execution.testing import parse_pytest_summary

    assert parse_pytest_summary("3 failed connections\n= 5 passed =") == {"passed": 5}


def test_parse_pytest_summary_handles_warnings():
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("= 100 passed, 5 warnings in 3s =")
    assert result.get("warning", 0) == 5
    assert result.get("passed", 0) == 100


def test_check_test_passed_true_on_zero_rc_clean_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 50 passed in 1s =") is True


def test_check_test_passed_false_on_nonzero_rc():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(1, "= 50 passed in 1s =") is False


def test_check_test_passed_false_on_zero_rc_with_failed_in_output():
    # Cross-validation: rc=0 but output says "failed" → False
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 2 failed, 48 passed =") is False


def test_check_test_passed_false_on_zero_rc_with_error_in_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 1 error =") is False


def test_check_test_passed_true_for_xfailed_skipped():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 97 passed, 3 xfailed, 1 skipped =") is True


# -- Bare -q format: parse_pytest_summary --


def test_parse_pytest_summary_bare_q_failed_and_passed():
    """pytest -q bare format: 'N failed, M passed in Ts' — no = delimiters."""
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("3 failed, 97 passed in 2.31s")
    assert result["failed"] == 3
    assert result["passed"] == 97


def test_parse_pytest_summary_bare_q_passed_only():
    """pytest -q bare format: 'N passed in Ts'."""
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("100 passed in 1.50s")
    assert result == {"passed": 100}


def test_parse_pytest_summary_bare_q_with_warnings():
    """pytest -q bare format with warnings: 'N passed, M warnings in Ts'."""
    from autoskillit.execution.testing import parse_pytest_summary

    result = parse_pytest_summary("97 passed, 5 warnings in 3.1s")
    assert result["passed"] == 97
    assert result.get("warning", 0) == 5


def test_parse_pytest_summary_bare_q_multiline_output():
    """Bare -q summary at end of multiline output (normal pytest -q run)."""
    from autoskillit.execution.testing import parse_pytest_summary

    output = (
        "FAILED tests/test_foo.py::test_a - AssertionError\n"
        "FAILED tests/test_foo.py::test_b - AssertionError\n"
        "2 failed, 98 passed in 4.12s\n"
    )
    result = parse_pytest_summary(output)
    assert result["failed"] == 2
    assert result["passed"] == 98


# -- CWA: check_test_passed with no summary line --


def test_check_test_passed_false_when_no_summary_empty_output():
    """CWA: rc=0 but output is empty — cannot confirm pass, return False."""
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "") is False


def test_check_test_passed_false_when_no_summary_log_only():
    """CWA: rc=0 but output has no pytest summary line — return False."""
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "collected 100 items\nsome log output\n") is False


def test_check_test_passed_false_bare_q_failures():
    """Bare -q output with failures: rc=0 (PIPESTATUS bug), still detects failure."""
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "3 failed, 97 passed in 2.31s") is False


def test_check_test_passed_true_bare_q_clean():
    """Bare -q all-passing output: rc=0 and summary found — return True."""
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "100 passed in 1.50s") is True
