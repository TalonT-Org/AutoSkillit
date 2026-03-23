"""Tests for smoke_utils — check_bug_report_non_empty callable."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.smoke_utils import check_bug_report_non_empty, check_cleanup_mode


# T_SU1
def test_returns_false_when_bug_report_missing(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json does not exist."""
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# T_SU2
def test_returns_false_when_bug_report_empty_array(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains []."""
    (tmp_path / "bug_report.json").write_text("[]")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# T_SU3
def test_returns_true_when_bug_report_has_items(tmp_path: Path) -> None:
    """Returns {"non_empty": "true"} when bug_report.json has at least one item."""
    (tmp_path / "bug_report.json").write_text(json.dumps([{"bug": "x"}]))
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "true"}


# T_SU4
def test_returns_false_when_bug_report_malformed(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains malformed JSON."""
    (tmp_path / "bug_report.json").write_text("{not valid json")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# T_SU5
def test_check_cleanup_mode_returns_deferred_true() -> None:
    """check_cleanup_mode('true') returns {'deferred': 'true'}."""
    assert check_cleanup_mode("true") == {"deferred": "true"}


# T_SU6
def test_check_cleanup_mode_returns_deferred_false() -> None:
    """check_cleanup_mode('false') returns {'deferred': 'false'}."""
    assert check_cleanup_mode("false") == {"deferred": "false"}


# T_SU7
def test_check_cleanup_mode_case_insensitive() -> None:
    """check_cleanup_mode accepts 'True', 'TRUE', 'False' etc."""
    assert check_cleanup_mode("True") == {"deferred": "true"}
    assert check_cleanup_mode("TRUE") == {"deferred": "true"}
    assert check_cleanup_mode("False") == {"deferred": "false"}
    assert check_cleanup_mode("FALSE") == {"deferred": "false"}
