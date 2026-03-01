"""L1 unit tests for execution/testing.py — pytest output parsing."""

from __future__ import annotations

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
