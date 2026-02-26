"""Tests for smoke_utils module."""

from __future__ import annotations

import json

from autoskillit.smoke_utils import check_bug_report_non_empty


def test_missing_file_returns_false(tmp_path):
    """No bug_report.json -> non_empty is false."""
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


def test_empty_list_returns_false(tmp_path):
    """bug_report.json with [] -> non_empty is false."""
    (tmp_path / "bug_report.json").write_text("[]")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


def test_non_empty_list_returns_true(tmp_path):
    """bug_report.json with one entry -> non_empty is true."""
    data = [{"step": "test", "error": "oops", "fix": "fixed it", "iteration": 1}]
    (tmp_path / "bug_report.json").write_text(json.dumps(data))
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "true"}


def test_invalid_json_returns_false(tmp_path):
    """bug_report.json with malformed JSON -> non_empty is false."""
    (tmp_path / "bug_report.json").write_text("not valid json {{{")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}
