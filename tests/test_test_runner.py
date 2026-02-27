"""Tests for test_runner.py public API."""


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
