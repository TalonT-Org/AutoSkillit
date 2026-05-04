"""Behavioral tests for execution/diff_annotator.py."""

from __future__ import annotations

import pytest

from autoskillit.execution.diff_annotator import (
    DiffMetrics,
    annotate_diff,
    compute_diff_metrics,
    filter_findings,
    parse_hunk_ranges,
    select_review_agents,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

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


# --- compute_diff_metrics ---


class TestComputeDiffMetrics:
    def test_counts_added_lines(self):
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,2 +1,4 @@\n"
            " existing\n"
            "+new_line_1\n"
            "+new_line_2\n"
            " more\n"
        )
        metrics = compute_diff_metrics(diff)
        assert metrics.added_lines == 2

    def test_counts_removed_lines(self):
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,1 @@\n"
            "-old_1\n"
            "-old_2\n"
            " kept\n"
        )
        metrics = compute_diff_metrics(diff)
        assert metrics.removed_lines == 2

    def test_counts_changed_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n+x\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
            "@@ -1,1 +1,2 @@\n+y\n"
            "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n"
            "@@ -1,1 +1,2 @@\n+z\n"
        )
        metrics = compute_diff_metrics(diff)
        assert metrics.changed_files == 3
        assert set(metrics.file_paths) == {"a.py", "b.py", "c.py"}

    def test_extracts_file_paths(self):
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+x\n"
        )
        metrics = compute_diff_metrics(diff)
        assert metrics.file_paths == ["src/app.py"]

    def test_empty_diff(self):
        metrics = compute_diff_metrics("")
        assert metrics.added_lines == 0
        assert metrics.removed_lines == 0
        assert metrics.changed_files == 0
        assert metrics.file_paths == []

    def test_ignores_diff_headers(self):
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n context\n"
        metrics = compute_diff_metrics(diff)
        assert metrics.added_lines == 0
        assert metrics.removed_lines == 0


# --- select_review_agents ---


class TestSelectReviewAgents:
    def test_small_diff_returns_core_agents(self):
        metrics = DiffMetrics(
            added_lines=50,
            removed_lines=10,
            changed_files=2,
            file_paths=["src/foo.py", "tests/test_foo.py"],
        )
        agents = select_review_agents(metrics)
        assert agents == ["tests", "cohesion"]

    def test_small_diff_with_structural_adds_arch(self):
        metrics = DiffMetrics(
            added_lines=30,
            removed_lines=5,
            changed_files=2,
            file_paths=["src/pkg/__init__.py", "src/pkg/mod.py"],
        )
        agents = select_review_agents(metrics)
        assert "arch" in agents
        assert "tests" in agents
        assert "cohesion" in agents
        assert len(agents) == 3

    def test_medium_diff_returns_all_agents(self):
        metrics = DiffMetrics(
            added_lines=200, removed_lines=50, changed_files=3, file_paths=["a.py", "b.py", "c.py"]
        )
        agents = select_review_agents(metrics)
        assert set(agents) == {"arch", "tests", "defense", "bugs", "cohesion", "slop"}

    def test_many_files_returns_all_agents(self):
        fps = [f"f{i}.py" for i in range(5)]
        metrics = DiffMetrics(added_lines=50, removed_lines=0, changed_files=5, file_paths=fps)
        agents = select_review_agents(metrics)
        assert set(agents) == {"arch", "tests", "defense", "bugs", "cohesion", "slop"}

    def test_custom_thresholds(self):
        metrics = DiffMetrics(
            added_lines=150, removed_lines=0, changed_files=3, file_paths=["a.py", "b.py", "c.py"]
        )
        assert len(select_review_agents(metrics)) < 6
        agents = select_review_agents(metrics, loc_threshold=100)
        assert set(agents) == {"arch", "tests", "defense", "bugs", "cohesion", "slop"}

    def test_deletion_regression_never_included(self):
        metrics = DiffMetrics(
            added_lines=500,
            removed_lines=200,
            changed_files=10,
            file_paths=[f"f{i}.py" for i in range(10)],
        )
        agents = select_review_agents(metrics)
        assert "deletion_regression" not in agents
