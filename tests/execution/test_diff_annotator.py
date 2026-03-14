"""Behavioral tests for execution/diff_annotator.py."""

from __future__ import annotations

from autoskillit.execution.diff_annotator import (
    annotate_diff,
    filter_findings,
    parse_hunk_ranges,
)

# --- parse_hunk_ranges ---


class TestParseHunkRanges:
    def test_single_file(self):
        """@@ -10,5 +12,8 @@ produces {"file.py": [(12, 19)]}."""
        diff = (
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -10,5 +12,8 @@\n"
            "+new line\n"
        )
        result = parse_hunk_ranges(diff)
        assert result == {"file.py": [(12, 19)]}

    def test_multi_hunk(self):
        """Multiple hunks in same file produce multiple ranges."""
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+added\n"
            "@@ -20,2 +21,5 @@\n"
            "+more\n"
        )
        result = parse_hunk_ranges(diff)
        assert result == {"f.py": [(1, 4), (21, 25)]}

    def test_multi_file(self):
        """Multiple files produce separate keys."""
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+line\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -5,1 +5,2 @@\n"
            "+line\n"
        )
        result = parse_hunk_ranges(diff)
        assert result == {"a.py": [(1, 3)], "b.py": [(5, 6)]}

    def test_skip_pure_deletion(self):
        """@@ -10,5 +0,0 @@ produces no range entry (pure deletion hunk)."""
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -10,5 +0,0 @@\n-deleted\n"
        result = parse_hunk_ranges(diff)
        assert result == {}

    def test_single_line_add(self):
        """@@ -10,0 +12,1 @@ produces [(12, 12)] for a single line addition."""
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -10,0 +12,1 @@\n+new\n"
        result = parse_hunk_ranges(diff)
        assert result == {"f.py": [(12, 12)]}


# --- annotate_diff ---


class TestAnnotateDiff:
    def test_adds_markers(self):
        """Each + and context line gets [LNNN] prefix with correct new-file line number."""
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -10,3 +10,4 @@\n"
            " line10\n"
            "+new11\n"
            " line12\n"
            "+new13\n"
        )
        result = annotate_diff(diff)
        assert "[L10] line10" in result
        assert "[L11]+new11" in result
        assert "[L12] line12" in result
        assert "[L13]+new13" in result

    def test_skips_minus_lines(self):
        """Deleted lines get no [LNNN] marker — they have no new-file line number."""
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -10,3 +10,2 @@\n"
            " context\n"
            "-removed\n"
            " after\n"
        )
        result = annotate_diff(diff)
        assert "[L10] context" in result
        assert "[L11] after" in result
        # Deleted line preserved verbatim without a marker
        for line in result.splitlines():
            if "removed" in line:
                assert line == "-removed"

    def test_resets_per_file(self):
        """Line numbering resets at each new file's @@ header."""
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            " lineA\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -5,1 +5,1 @@\n"
            " lineB\n"
        )
        result = annotate_diff(diff)
        assert "[L1] lineA" in result
        assert "[L5] lineB" in result

    def test_preserves_hunk_headers(self):
        """@@ headers pass through unchanged for subagent reference."""
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -10,3 +10,4 @@\n+line\n"
        result = annotate_diff(diff)
        assert "@@ -10,3 +10,4 @@" in result


# --- filter_findings ---


class TestFilterFindings:
    def test_correct_partition(self):
        """Findings in range are filtered; out of range are unpostable."""
        ranges = {"f.py": [(10, 20)]}
        findings = [
            {"file": "f.py", "line": 15, "message": "ok"},
            {"file": "f.py", "line": 500, "message": "bad"},
        ]
        result = filter_findings(findings, ranges)
        assert len(result.filtered) == 1
        assert len(result.unpostable) == 1

    def test_boundary_inclusive(self):
        """Lines at exact start and end of range are FILTERED."""
        ranges = {"f.py": [(10, 20)]}
        findings = [
            {"file": "f.py", "line": 10, "message": "start"},
            {"file": "f.py", "line": 20, "message": "end"},
        ]
        result = filter_findings(findings, ranges)
        assert len(result.filtered) == 2
        assert len(result.unpostable) == 0

    def test_all_unpostable_signals_failure(self):
        """When all findings are unpostable (total > 0), result.all_unpostable is True."""
        ranges = {"f.py": [(10, 20)]}
        findings = [{"file": "f.py", "line": 500, "message": "bad"}]
        result = filter_findings(findings, ranges)
        assert result.all_unpostable is True

    def test_empty_ranges_passes_all(self):
        """When VALID_LINE_RANGES is empty, all findings pass (no filtering possible)."""
        findings = [{"file": "f.py", "line": 999, "message": "ok"}]
        result = filter_findings(findings, {})
        assert len(result.filtered) == 1
        assert result.all_unpostable is False

    def test_no_findings_no_failure(self):
        """Zero findings produces all_unpostable=False (nothing to post is not a failure)."""
        result = filter_findings([], {"f.py": [(10, 20)]})
        assert result.all_unpostable is False


# --- End-to-end ---


class TestEndToEnd:
    def test_annotate_and_filter(self):
        """Parse a realistic multi-file diff, annotate it, simulate findings
        with correct [LNNN] numbers, and verify they pass the filter."""
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -10,3 +10,5 @@ def main():\n"
            " existing_line\n"
            "+new_import\n"
            "+another_import\n"
            " more_existing\n"
            "@@ -50,2 +52,3 @@ def helper():\n"
            " context\n"
            "+added_call\n"
            "diff --git a/tests/test_app.py b/tests/test_app.py\n"
            "--- a/tests/test_app.py\n"
            "+++ b/tests/test_app.py\n"
            "@@ -1,2 +1,4 @@\n"
            " import pytest\n"
            "+from app import main\n"
            "+from app import helper\n"
            " \n"
        )

        # Parse ranges
        ranges = parse_hunk_ranges(diff)
        assert "src/app.py" in ranges
        assert "tests/test_app.py" in ranges
        assert ranges["src/app.py"] == [(10, 14), (52, 54)]
        assert ranges["tests/test_app.py"] == [(1, 4)]

        # Annotate
        annotated = annotate_diff(diff)
        assert "[L10] existing_line" in annotated
        assert "[L11]+new_import" in annotated
        assert "[L12]+another_import" in annotated
        assert "[L13] more_existing" in annotated
        assert "[L52] context" in annotated
        assert "[L53]+added_call" in annotated
        assert "[L1] import pytest" in annotated
        assert "[L2]+from app import main" in annotated

        # Simulate findings using [LNNN] numbers (correct) vs stream offsets (wrong)
        findings = [
            {"file": "src/app.py", "line": 11, "message": "correct marker"},
            {"file": "src/app.py", "line": 53, "message": "correct marker"},
            {"file": "tests/test_app.py", "line": 2, "message": "correct marker"},
            {"file": "src/app.py", "line": 9999, "message": "stream offset"},
        ]

        result = filter_findings(findings, ranges)
        assert len(result.filtered) == 3
        assert len(result.unpostable) == 1
        assert result.unpostable[0]["message"] == "stream offset"
        assert result.all_unpostable is False
