"""Tests for content-aware Bucket A check and build_test_scope integration (T1, T2)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests._test_filter import (
    FilterMode,
    build_test_scope,
)

# ---------------------------------------------------------------------------
# Content-Aware Bucket A Tests (T1)
# ---------------------------------------------------------------------------


class TestCheckBucketAContentAware:
    def test_content_aware_version_only_pyproject_not_triggered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pyproject.toml with only version= line change: content-aware check returns False."""
        from tests._test_filter import check_bucket_a_content_aware

        diff_output = (
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -5 +5 @@\n"
            '-version = "0.9.107"\n+version = "0.9.108"\n'
        )
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),  # merge-base
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),  # diff
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = check_bucket_a_content_aware({"pyproject.toml"}, "/fake", "main")
        assert result is False

    def test_content_aware_uv_lock_version_only_not_triggered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """uv.lock with only version= line change: content-aware check returns False."""
        from tests._test_filter import check_bucket_a_content_aware

        diff_output = (
            "--- a/uv.lock\n+++ b/uv.lock\n@@ -10 +10 @@\n"
            '-version = "0.9.107"\n+version = "0.9.108"\n'
        )
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = check_bucket_a_content_aware({"uv.lock"}, "/fake", "main")
        assert result is False

    def test_content_aware_pyproject_structural_change_triggers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pyproject.toml with non-version line change: content-aware check returns True."""
        from tests._test_filter import check_bucket_a_content_aware

        diff_output = (
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n"
            '@@ -5 +5 @@\n-version = "0.9.107"\n+version = "0.9.108"\n'
            '@@ -20 +20 @@\n-requires-python = ">=3.11"\n+requires-python = ">=3.12"\n'
        )
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = check_bucket_a_content_aware({"pyproject.toml"}, "/fake", "main")
        assert result is True

    def test_content_aware_git_failure_falls_back_to_full_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Git failure: content-aware check returns True (fail-open)."""
        from tests._test_filter import check_bucket_a_content_aware

        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = check_bucket_a_content_aware({"pyproject.toml"}, "/fake", "main")
        assert result is True

    def test_content_aware_other_bucket_a_pattern_unaffected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Other Bucket A patterns (not pyproject/uv.lock) trigger immediately, no git call."""
        from tests._test_filter import check_bucket_a_content_aware

        mock_run = Mock()
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = check_bucket_a_content_aware({"tests/conftest.py"}, "/fake", "main")
        assert result is True
        mock_run.assert_not_called()  # no git diff needed

    def test_content_aware_version_only_both_files_not_triggered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both pyproject.toml and uv.lock with version-only changes: returns False."""
        from tests._test_filter import check_bucket_a_content_aware

        diff_output = (
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -5 +5 @@\n"
            '-version = "0.9.107"\n+version = "0.9.108"\n'
            "--- a/uv.lock\n+++ b/uv.lock\n@@ -10 +10 @@\n"
            '-version = "0.9.107"\n+version = "0.9.108"\n'
        )
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = check_bucket_a_content_aware({"pyproject.toml", "uv.lock"}, "/fake", "main")
        assert result is False


# ---------------------------------------------------------------------------
# build_test_scope content-aware integration tests (T2)
# ---------------------------------------------------------------------------


class TestBuildTestScopeContentAware:
    def test_scope_pyproject_version_only_with_cwd_no_full_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """build_test_scope: pyproject.toml version-only with cwd= does NOT force full run."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        diff_output = (
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -5 +5 @@\n"
            '-version = "0.9.107"\n+version = "0.9.108"\n'
        )
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = build_test_scope(
            changed_files={"pyproject.toml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=str(tmp_path),
            base_ref="main",
        )
        assert result is not None  # filtered run, not full suite
        assert result == {tests_root / d for d in ["arch", "contracts", "infra", "docs"]}

    def test_scope_pyproject_without_cwd_still_full_run(self, tmp_path: Path) -> None:
        """build_test_scope: pyproject.toml without cwd= still forces full run."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        result = build_test_scope(
            changed_files={"pyproject.toml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            # no cwd or base_ref
        )
        assert result is None  # still full run without cwd
