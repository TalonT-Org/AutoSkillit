"""Tests for content-aware Bucket A check and build_test_scope integration (T1–T5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests._test_filter import (
    _CORE_UNIVERSAL_EXCLUSIONS,
    _CORE_UNIVERSAL_MODULES,
    LAYER_CASCADE_CONSERVATIVE,
    FilterMode,
    FullRunReason,
    _is_additive_only,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]

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
        assert result is FullRunReason.BUCKET_A  # still full run without cwd


# ---------------------------------------------------------------------------
# _is_additive_only unit tests (T3)
# ---------------------------------------------------------------------------


class TestIsAdditiveOnly:
    def test_additive_only_new_class(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pure addition of a new class: returns True."""
        diff_output = "+class NewType:\n+    field: str"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is True

    def test_additive_only_new_field(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adding a field to existing TypedDict: returns True."""
        diff_output = "+    new_field: int"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is True

    def test_removal_detected_class(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Removed class definition: returns False, triggers full cascade."""
        diff_output = "-class OldType:\n-    field: str"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_removal_detected_function(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Removed function definition: returns False."""
        diff_output = "-def old_func():"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_removal_detected_constant(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Removed UPPER_CASE constant: returns False."""
        diff_output = "-SOME_CONST = 42"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_rename_detected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Class rename (removal + addition): returns False."""
        diff_output = "-class OldName:\n+class NewName:"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_mixed_diff_with_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mixed diff with a removal: returns False."""
        diff_output = "+class New:\n-class Old:"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_empty_diff_is_additive(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty diff: returns True (vacuously additive)."""
        diff_output = ""
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is True

    def test_git_error_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Git failure: fail-open returns False (full cascade used)."""

        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_invalid_sha_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """merge-base returns invalid SHA: fail-open returns False."""
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="not-a-sha\n"),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False

    def test_lowercase_field_change_is_additive(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lowercase field type change (no +/- prefix match): treated as additive."""
        diff_output = "-    name: str\n+    name: str | None"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is True

    def test_indented_class_removal_detected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Indented class removal still matches the regex: returns False."""
        diff_output = "-    class InnerClass:"
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_output),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = _is_additive_only("/fake", "main", "src/core/_type_enums.py")
        assert result is False


# ---------------------------------------------------------------------------
# build_test_scope integration tests for content-aware cascade (T4)
# ---------------------------------------------------------------------------


class TestBuildTestScopeUniversalExclusions:
    ALL_DIRS = [
        "core",
        "config",
        "execution",
        "pipeline",
        "workspace",
        "recipe",
        "migration",
        "fleet",
        "server",
        "cli",
        "hooks",
        "skills",
        "_llm_triage",
        "_test_filter",
        "hook_registry",
        "planner",
        "smoke_utils",
        "arch",
        "contracts",
        "infra",
        "docs",
    ]

    @staticmethod
    def _make_tests_root(tmp_path: Path) -> Path:
        tests_root = tmp_path / "tests"
        for d in TestBuildTestScopeUniversalExclusions.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_universal_additive_only_uses_narrowed_cascade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_type_enums with additive-only diff: cascade excludes hooks and skills."""
        tests_root = self._make_tests_root(tmp_path)
        diff_output = "+class NewType:\n+    field: str"
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
            changed_files={"src/autoskillit/core/_type_enums.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=str(tmp_path),
            base_ref="main",
        )
        assert isinstance(result, set)
        dir_names = {p.name for p in result if p.is_dir()}
        for pkg in ["core", "config", "execution", "pipeline", "server", "cli"]:
            assert pkg in dir_names, f"narrowed cascade should include {pkg}"
        for excluded in ["hooks", "skills"]:
            assert excluded not in dir_names, f"narrowed cascade should exclude {excluded}"

    def test_universal_breaking_change_uses_full_cascade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_type_enums with removal in diff: uses full cascade."""
        tests_root = self._make_tests_root(tmp_path)
        diff_output = "-class OldType:"
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
            changed_files={"src/autoskillit/core/_type_enums.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=str(tmp_path),
            base_ref="main",
        )
        assert isinstance(result, set)
        dir_names = {p.name for p in result if p.is_dir()}
        for pkg in ["core", "config", "execution", "hooks", "skills"]:
            assert pkg in dir_names, f"full cascade should include {pkg}"

    def test_universal_without_exclusion_entry_uses_full_cascade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """io.py changed (universal but no exclusion entry): full cascade."""
        tests_root = self._make_tests_root(tmp_path)
        diff_output = "+class NewType:"
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
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=str(tmp_path),
            base_ref="main",
        )
        assert isinstance(result, set)
        dir_names = {p.name for p in result if p.is_dir()}
        for pkg in ["core", "config", "execution", "hooks", "skills"]:
            assert pkg in dir_names, f"full cascade should include {pkg}"
        mock_run.assert_not_called()

    def test_universal_without_cwd_uses_full_cascade(
        self,
        tmp_path: Path,
    ) -> None:
        """_type_enums with additive diff but no cwd/base_ref: full cascade."""
        tests_root = self._make_tests_root(tmp_path)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_type_enums.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert isinstance(result, set)
        dir_names = {p.name for p in result if p.is_dir()}
        for pkg in ["core", "config", "execution", "hooks", "skills"]:
            assert pkg in dir_names, f"full cascade should include {pkg}"

    def test_universal_git_error_uses_full_cascade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_type_enums with git error: full cascade (fail-open)."""
        tests_root = self._make_tests_root(tmp_path)

        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_type_enums.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=str(tmp_path),
            base_ref="main",
        )
        assert isinstance(result, set)
        dir_names = {p.name for p in result if p.is_dir()}
        for pkg in ["core", "config", "execution", "hooks", "skills"]:
            assert pkg in dir_names, f"full cascade should include {pkg}"


# ---------------------------------------------------------------------------
# _CORE_UNIVERSAL_EXCLUSIONS contract tests (T5)
# ---------------------------------------------------------------------------


class TestCoreUniversalExclusions:
    def test_exclusions_dict_exists(self) -> None:
        """_CORE_UNIVERSAL_EXCLUSIONS is a dict."""
        assert isinstance(_CORE_UNIVERSAL_EXCLUSIONS, dict)

    def test_all_exclusion_keys_are_strings(self) -> None:
        """All keys in _CORE_UNIVERSAL_EXCLUSIONS are strings."""
        assert all(isinstance(k, str) for k in _CORE_UNIVERSAL_EXCLUSIONS)

    def test_all_exclusion_values_are_frozensets(self) -> None:
        """All values in _CORE_UNIVERSAL_EXCLUSIONS are frozenset[str]."""
        assert all(
            isinstance(v, frozenset) and all(isinstance(x, str) for x in v)
            for v in _CORE_UNIVERSAL_EXCLUSIONS.values()
        )

    def test_type_enums_exclusion(self) -> None:
        """_type_enums maps to hooks and skills."""
        assert _CORE_UNIVERSAL_EXCLUSIONS.get("_type_enums") == frozenset({"hooks", "skills"})

    def test_exclusion_keys_are_universal_modules(self) -> None:
        """Every key in _CORE_UNIVERSAL_EXCLUSIONS is in _CORE_UNIVERSAL_MODULES."""
        assert all(stem in _CORE_UNIVERSAL_MODULES for stem in _CORE_UNIVERSAL_EXCLUSIONS)

    def test_exclusion_dirs_are_subset_of_core_cascade(self) -> None:
        """Every exclusion dir is in LAYER_CASCADE_CONSERVATIVE[core]."""
        core_dirs = LAYER_CASCADE_CONSERVATIVE["core"]
        for stem, exclusions in _CORE_UNIVERSAL_EXCLUSIONS.items():
            assert exclusions <= core_dirs, (
                f"{stem} exclusion {exclusions} is not a subset of core cascade {core_dirs}"
            )
