"""Tests for tests/_test_filter.py — standalone test-path filtering logic."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

import tests._test_filter as tf_mod
from autoskillit._test_filter import apply_manifest as manifest_apply_manifest
from autoskillit._test_filter import load_manifest as manifest_load_manifest
from tests._test_filter import (
    ALWAYS_RUN_AGGRESSIVE,
    ALWAYS_RUN_CONSERVATIVE,
    LAYER_CASCADE_AGGRESSIVE,
    LAYER_CASCADE_CONSERVATIVE,
    ASTImportWalker,
    FilterMode,
    ImportContext,
    _expand_reexport_closure,
    apply_manifest,
    build_test_scope,
    check_bucket_a,
    git_changed_files,
    load_coverage_map,
    load_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / ".autoskillit" / "test-filter-manifest.yaml"

# ---------------------------------------------------------------------------
# Walker Tests (W1–W8)
# ---------------------------------------------------------------------------


class TestASTImportWalker:
    def test_walker_top_level_import(self) -> None:
        tree = ast.parse("import os")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("os", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_top_level_from_import(self) -> None:
        tree = ast.parse("from pathlib import Path")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("pathlib", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_relative_from_import(self) -> None:
        tree = ast.parse("from .sub import X")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert (".sub", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_conditional_import(self) -> None:
        source = "import sys\nif sys.platform == 'linux':\n    import foo"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.CONDITIONAL) in walker.imports

    def test_walker_type_checking_guard(self) -> None:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from foo import Bar"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.TYPE_CHECKING) in walker.imports

    def test_walker_type_checking_attribute(self) -> None:
        source = "import typing\nif typing.TYPE_CHECKING:\n    from bar import Baz"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("bar", ImportContext.TYPE_CHECKING) in walker.imports

    def test_walker_deferred_import(self) -> None:
        source = "def f():\n    import foo"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.DEFERRED) in walker.imports

    def test_walker_importlib_literal(self) -> None:
        source = 'import importlib\nimportlib.import_module("foo")'
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.IMPORTLIB) in walker.imports


# ---------------------------------------------------------------------------
# Bucket A Tests (B1–B9)
# ---------------------------------------------------------------------------


class TestCheckBucketA:
    def test_bucket_a_conftest(self) -> None:
        assert check_bucket_a({"tests/conftest.py"}) is True

    def test_bucket_a_helpers(self) -> None:
        assert check_bucket_a({"tests/_helpers.py"}) is True

    def test_bucket_a_arch_helpers(self) -> None:
        assert check_bucket_a({"tests/arch/_helpers.py"}) is True
        assert check_bucket_a({"tests/arch/_rules.py"}) is True

    def test_bucket_a_pyproject(self) -> None:
        assert check_bucket_a({"pyproject.toml"}) is True

    def test_bucket_a_uv_lock(self) -> None:
        assert check_bucket_a({"uv.lock"}) is True

    def test_bucket_a_precommit(self) -> None:
        assert check_bucket_a({".pre-commit-config.yaml"}) is True

    def test_bucket_a_factory(self) -> None:
        assert check_bucket_a({"src/autoskillit/server/_factory.py"}) is True

    def test_bucket_a_subdir_conftest(self) -> None:
        assert check_bucket_a({"tests/execution/conftest.py"}) is True

    def test_bucket_a_negative(self) -> None:
        assert check_bucket_a({"src/autoskillit/core/io.py"}) is False


# ---------------------------------------------------------------------------
# build_test_scope Tests (S1–S10)
# ---------------------------------------------------------------------------


class TestBuildTestScope:
    def test_scope_none_changed_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files=None,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_large_changeset_returns_none(self, tmp_path: Path) -> None:
        files = {f"src/autoskillit/core/f{i}.py" for i in range(31)}
        result = build_test_scope(
            changed_files=files,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_bucket_a_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"pyproject.toml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_l0_core_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in [
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
            "skills",
            "arch",
            "contracts",
            "infra",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for expected in [
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
            "skills",
        ]:
            assert expected in dir_names, f"{expected} missing from cascade"
        assert "arch" in dir_names, "arch always-run missing"
        assert "contracts" in dir_names, "contracts always-run missing"
        # infra and docs are not directories for a non-triggering change;
        # 9 individual infra files appear in result via direct_test_files
        result_names = {p.name for p in result}
        from tests._test_filter import _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        assert "test_doc_counts.py" in result_names

    def test_scope_l1_execution_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in [
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
            "infra",
            "skills",
            "arch",
            "contracts",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for expected in [
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
            "infra",
            "skills",
        ]:
            assert expected in dir_names, f"{expected} missing from cascade"

    def test_scope_l2_recipe_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["recipe", "arch", "contracts", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        for f in [
            "server/test_factory.py",
            "server/test_tools_load_recipe.py",
            "server/test_tools_kitchen.py",
            "cli/test_cli_prompts.py",
            "cli/test_cook.py",
            "execution/test_headless_path_validation.py",
            "execution/test_zero_write_detection.py",
            "migration/test_api.py",
            "migration/test_engine.py",
            "hooks/test_recipe_write_advisor.py",
            "infra/test_pretty_output.py",
            "skills/test_planner_skill_contracts.py",
            "skills/test_skill_placeholder_contracts.py",
            "skills/test_make_campaign_compliance.py",
            "skills/test_review_design_guards.py",
            "skills/test_skill_tool_syntax_contracts.py",
            "core/test_type_constants.py",
            "core/test_kitchen_state.py",
            "core/test_session_registry.py",
        ]:
            p = tests_root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        (tests_root / "test_llm_triage.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        assert "recipe" in result_names, "recipe missing"
        for expected in [
            "test_factory.py",
            "test_tools_load_recipe.py",
            "test_tools_kitchen.py",
            "test_cli_prompts.py",
            "test_cook.py",
            "test_headless_path_validation.py",
            "test_zero_write_detection.py",
            "test_pretty_output.py",
            "test_skill_placeholder_contracts.py",
            "test_recipe_write_advisor.py",
        ]:
            assert expected in result_names, f"{expected} missing"
        for absent in [
            "execution",
            "infra",
            "skills",
            "core",
            "server",
            "cli",
            "migration",
            "hooks",
        ]:
            assert absent not in result_names, f"{absent} should not be a full directory"

    def test_scope_l3_server_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        fleet_dir = tests_root / "fleet"
        for d in ["server", "cli", "fleet", "infra", "arch", "contracts", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        (fleet_dir / "test_pack_enforcement.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/server/helpers.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        assert "server" in result_names
        assert "cli" in result_names
        assert "test_pack_enforcement.py" in result_names
        from tests._test_filter import _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        assert "infra" not in {p.name for p in result if (tests_root / p.name).is_dir()}, (
            "full infra dir should not appear for pure server change"
        )

    def test_scope_test_file_included_directly(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"tests/core/test_io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        assert Path("tests/core/test_io.py") in result

    def test_scope_nonpython_no_manifest_only_alwaysrun(self, tmp_path: Path) -> None:
        """Non-Python file with manifest=None → fail-open → result is None."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"README.md"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is None

    def test_scope_nonpython_unmatched_manifest_returns_none(self, tmp_path: Path) -> None:
        """A non-Python changed file with no manifest match must return None (full run)."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        manifest = {"docs/**/*.md": ["docs"]}
        result = build_test_scope(
            changed_files={"some/unknown/file.txt"},
            mode=FilterMode.CONSERVATIVE,
            manifest=manifest,
            tests_root=tests_root,
        )
        assert result is None

    def test_scope_nonpython_matched_manifest_adds_dirs(self, tmp_path: Path) -> None:
        """A non-Python file that DOES match a manifest pattern contributes its test dirs."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        manifest = {"docs/**/*.md": ["docs"]}
        result = build_test_scope(
            changed_files={"docs/README.md"},
            mode=FilterMode.CONSERVATIVE,
            manifest=manifest,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "arch" in dir_names
        assert "contracts" in dir_names
        assert "docs" in dir_names
        assert "infra" not in dir_names  # docs change doesn't trigger full infra
        result_names = {p.name for p in result}
        from tests._test_filter import _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names

    def test_scope_none_mode_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.NONE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_empty_changeset(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files=set(),
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert dir_names == {"arch", "contracts", "infra", "docs"}


# ---------------------------------------------------------------------------
# Conservative vs Aggressive Tests (M1–M4)
# ---------------------------------------------------------------------------


class TestFilterModes:
    def test_conservative_always_run_includes_infra(self) -> None:
        assert "arch" in ALWAYS_RUN_CONSERVATIVE
        assert "contracts" in ALWAYS_RUN_CONSERVATIVE
        assert "infra" in ALWAYS_RUN_CONSERVATIVE
        assert "docs" in ALWAYS_RUN_CONSERVATIVE

    def test_aggressive_always_run_excludes_infra(self) -> None:
        assert "arch" in ALWAYS_RUN_AGGRESSIVE
        assert "contracts" in ALWAYS_RUN_AGGRESSIVE
        assert "infra" not in ALWAYS_RUN_AGGRESSIVE
        assert "docs" not in ALWAYS_RUN_AGGRESSIVE

    def test_aggressive_ast_refinement(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        result_aggressive = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result_aggressive is not None
        dir_names = {p.name for p in result_aggressive}
        assert "core" in dir_names
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_conservative_wider_cascade(self) -> None:
        for pkg in LAYER_CASCADE_CONSERVATIVE:
            if pkg in LAYER_CASCADE_AGGRESSIVE:
                assert LAYER_CASCADE_AGGRESSIVE[pkg] <= LAYER_CASCADE_CONSERVATIVE[pkg], (
                    f"Aggressive cascade for {pkg} is not a subset of conservative"
                )


# ---------------------------------------------------------------------------
# Git Diff Edge Cases (G1–G5)
# ---------------------------------------------------------------------------


class TestGitChangedFiles:
    def test_git_changed_files_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="src/autoskillit/core/io.py\ntests/core/test_io.py\n",
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files("/fake", base_ref="main")
        assert result == {"src/autoskillit/core/io.py", "tests/core/test_io.py"}
        assert mock_run.call_count == 3

    def test_git_changed_files_failure_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files("/fake", base_ref="main")
        assert result is None

    def test_git_changed_files_timeout_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files("/fake", base_ref="main")
        assert result is None

    def test_git_changed_files_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "feature-branch")
        monkeypatch.delenv("GITHUB_BASE_REF", raising=False)

        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        git_changed_files("/fake")
        assert mock_run.call_count == 3
        first_call_args = list(mock_run.call_args_list[0][0][0])
        assert first_call_args[:3] == ["git", "merge-base", "HEAD"]
        assert first_call_args[3] == "feature-branch"

    def test_git_changed_files_github_base_ref(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_TEST_BASE_REF", raising=False)
        monkeypatch.setenv("GITHUB_BASE_REF", "main")

        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        git_changed_files("/fake")
        assert mock_run.call_count == 3
        first_call_args = list(mock_run.call_args_list[0][0][0])
        assert first_call_args[:3] == ["git", "merge-base", "HEAD"]
        assert first_call_args[3] == "main"

    def test_git_changed_files_includes_unstaged_tracked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="src/autoskillit/core/io.py\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files("/fake", base_ref="main")
        assert result == {"src/autoskillit/core/io.py"}
        assert mock_run.call_count == 3

    def test_git_changed_files_includes_untracked_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="new_script.py\n"),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files("/fake", base_ref="main")
        assert result == {"new_script.py"}
        assert mock_run.call_count == 3

    def test_git_changed_files_ls_files_failure_is_nonfatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="src/autoskillit/core/io.py\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=1, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files("/fake", base_ref="main")
        assert result == {"src/autoskillit/core/io.py"}
        assert mock_run.call_count == 3


# ---------------------------------------------------------------------------
# Re-export Closure Tests (R1–R2)
# ---------------------------------------------------------------------------


class TestReexportClosure:
    def test_reexport_closure_direct_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "sub.py").write_text("x = 1\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/sub.py"}, tmp_path)
        assert "pkg/__init__.py" in result
        assert "pkg/sub.py" in result

    def test_reexport_closure_no_match(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "other.py").write_text("y = 2\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/other.py"}, tmp_path)
        assert "pkg/__init__.py" not in result
        assert "pkg/other.py" in result


# ---------------------------------------------------------------------------
# Re-export Closure Integration Tests (build_test_scope wiring)
# ---------------------------------------------------------------------------


class TestReexportClosureIntegration:
    def test_build_test_scope_calls_expand_reexport_closure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[set[str]] = []
        original = tf_mod._expand_reexport_closure

        def spy(changed_src_files: set[str], src_root: object) -> set[str]:
            captured.append(set(changed_src_files))
            return original(changed_src_files, src_root)

        monkeypatch.setattr(tf_mod, "_expand_reexport_closure", spy)

        tests_root = tmp_path / "tests"
        for d in list(LAYER_CASCADE_AGGRESSIVE["core"]) + list(ALWAYS_RUN_AGGRESSIVE):
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )

        assert captured, "_expand_reexport_closure was not called from build_test_scope"
        assert "src/autoskillit/core/io.py" in captured[0]
        assert result is not None
        assert tests_root / "core" in result

    def test_core_io_change_expands_to_core_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integration: changing core/io.py expands to include core/__init__.py."""
        core_src = tmp_path / "src" / "autoskillit" / "core"
        core_src.mkdir(parents=True)
        (core_src / "io.py").write_text("# io\n")
        (core_src / "__init__.py").write_text("from .io import atomic_write\n")

        captured_expanded: list[set[str]] = []
        original = tf_mod._expand_reexport_closure

        def spy(changed_src_files: set[str], src_root: object) -> set[str]:
            result = original(changed_src_files, src_root)
            captured_expanded.append(set(result))
            return result

        monkeypatch.setattr(tf_mod, "_expand_reexport_closure", spy)

        tests_root = tmp_path / "tests"
        for d in list(LAYER_CASCADE_CONSERVATIVE["core"]) + list(ALWAYS_RUN_CONSERVATIVE):
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )

        assert result is not None
        assert captured_expanded, "_expand_reexport_closure was not called"
        assert "src/autoskillit/core/__init__.py" in captured_expanded[0], (
            "core/__init__.py was not found in expansion of core/io.py"
        )

    def test_expansion_error_is_fail_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(changed_src_files: set[str], src_root: object) -> set[str]:
            raise RuntimeError("simulated expansion failure")

        monkeypatch.setattr(tf_mod, "_expand_reexport_closure", explode)

        tests_root = tmp_path / "tests"
        for d in list(LAYER_CASCADE_AGGRESSIVE["core"]) + list(ALWAYS_RUN_AGGRESSIVE):
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        # Expansion error must NOT propagate; scope still computed from original classification
        assert result is not None
        assert tests_root / "core" in result

    def test_unclassifiable_init_does_not_cause_full_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An __init__.py added by expansion that maps to no cascade entry is silently skipped."""

        tests_root = tmp_path / "tests"
        for d in list(LAYER_CASCADE_AGGRESSIVE["core"]) + list(ALWAYS_RUN_AGGRESSIVE):
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        baseline = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )

        # Expansion returns an __init__.py outside any known package
        def always_expand(changed_src_files: set[str], src_root: object) -> set[str]:
            return set(changed_src_files) | {"src/autoskillit/__init__.py"}

        monkeypatch.setattr(tf_mod, "_expand_reexport_closure", always_expand)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        # Must NOT return None — unclassifiable init.py is skipped, not a fail-open trigger
        assert result is not None
        # The unclassifiable init.py must not expand the scope beyond the base classification
        assert result == baseline


# ---------------------------------------------------------------------------
# Manifest Tests — tests/_test_filter (MA1–MA4)
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_load_manifest_absent(self, tmp_path: Path) -> None:
        result = load_manifest(tmp_path)
        assert result is None

    def test_load_manifest_valid(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".autoskillit"
        manifest_dir.mkdir()
        (manifest_dir / "test-filter-manifest.yaml").write_text(
            "patterns:\n  'docs/*.md':\n    - docs\n"
        )
        result = load_manifest(tmp_path)
        assert result is not None
        assert "patterns" in result
        assert "docs/*.md" in result["patterns"]

    def test_load_manifest_malformed_yaml(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".autoskillit"
        manifest_dir.mkdir()
        (manifest_dir / "test-filter-manifest.yaml").write_text(":\n  - :\n  bad: [")
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_manifest(tmp_path)
        assert result is None
        assert any("Malformed YAML" in str(warning.message) for warning in w)


class TestApplyManifest:
    def test_apply_manifest_none(self) -> None:
        result = apply_manifest({"README.md"}, None)
        assert result is None

    def test_apply_manifest_match(self) -> None:
        manifest = {"docs/*.md": ["docs"]}
        result = apply_manifest({"docs/README.md"}, manifest)
        assert result == {"docs"}

    def test_apply_manifest_no_match(self) -> None:
        manifest = {"tests/*.py": ["unit"]}
        result = apply_manifest({"src/foo.py"}, manifest)
        assert result is None

    def test_apply_manifest_list_dirs(self) -> None:
        manifest = {"*.yaml": ["config", "infra"]}
        result = apply_manifest({"defaults.yaml"}, manifest)
        assert result == {"config", "infra"}

    def test_apply_manifest_doublestar_zero_segments(self) -> None:
        """docs/**/*.md must match docs/README.md (zero intermediate path segments).

        This pattern is in the production manifest. fnmatch fails this; pathspec passes it.
        """
        manifest = {"docs/**/*.md": ["docs"]}
        result = apply_manifest({"docs/README.md"}, manifest)
        assert result == {"docs"}

    def test_apply_manifest_doublestar_nested(self) -> None:
        """docs/**/*.md must match docs/developer/SETUP.md (non-zero intermediate segments)."""
        manifest = {"docs/**/*.md": ["docs"]}
        result = apply_manifest({"docs/developer/SETUP.md"}, manifest)
        assert result == {"docs"}


# ---------------------------------------------------------------------------
# Behavioral equivalence cross-validation (EQ1–EQ2)
# ---------------------------------------------------------------------------


class TestApplyManifestEquivalence:
    """Cross-validates conftest apply_manifest against production manifest_apply_manifest.

    Both modules must agree on matched outputs. When a file matches patterns in one,
    it must match the same patterns in the other. The two implementations are allowed
    to differ only in how they handle the unmatched case (both must return None there too).
    """

    MANIFEST = {
        "src/autoskillit/recipes/*.yaml": ["recipe", "contracts"],
        "docs/**/*.md": ["docs"],
        "Taskfile.yml": ["infra"],
        "tests/recipe/fixtures/*": ["recipe"],
        ".pre-commit-config.yaml": ["infra"],
    }

    @pytest.mark.parametrize(
        "file_path,expected_dirs",
        [
            ("src/autoskillit/recipes/my.yaml", {"recipe", "contracts"}),
            ("docs/README.md", {"docs"}),
            ("docs/sub/dir/deep.md", {"docs"}),
            ("Taskfile.yml", {"infra"}),
            ("tests/recipe/fixtures/test.yaml", {"recipe"}),
            (".pre-commit-config.yaml", {"infra"}),
        ],
    )
    def test_matching_cases_agree(self, file_path: str, expected_dirs: set[str]) -> None:
        """For files that match manifest patterns, both implementations return the same dirs."""
        conftest_result = apply_manifest({file_path}, self.MANIFEST)
        production_result = manifest_apply_manifest([file_path], self.MANIFEST)
        assert conftest_result == production_result == expected_dirs, (
            f"Implementations disagree for {file_path!r}: "
            f"conftest={conftest_result!r}, production={production_result!r}"
        )

    @pytest.mark.parametrize(
        "file_path",
        [
            "some/unknown/file.txt",
            "README.md",
            "pyproject.toml",
        ],
    )
    def test_unmatched_cases_both_return_none(self, file_path: str) -> None:
        """For files matching no manifest pattern, both implementations return None."""
        conftest_result = apply_manifest({file_path}, self.MANIFEST)
        production_result = manifest_apply_manifest([file_path], self.MANIFEST)
        assert conftest_result is None, f"conftest returned {conftest_result!r} for {file_path!r}"
        assert production_result is None, (
            f"production returned {production_result!r} for {file_path!r}"
        )


# ---------------------------------------------------------------------------
# Manifest Tests — autoskillit._test_filter (pathspec-based)
# ---------------------------------------------------------------------------


class TestManifestLoadManifest:
    def test_load_manifest_parses_yaml(self) -> None:
        manifest = manifest_load_manifest(MANIFEST_PATH)
        assert isinstance(manifest, dict)
        assert len(manifest) >= 22
        for pattern, dirs in manifest.items():
            assert isinstance(dirs, list)
            assert len(dirs) > 0
            assert all(isinstance(d, str) for d in dirs)

    def test_load_manifest_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            manifest_load_manifest(tmp_path / "nonexistent.yaml")


class TestManifestApplyManifest:
    def test_apply_manifest_single_star_glob(self) -> None:
        manifest = {"src/autoskillit/recipes/*.yaml": ["recipe/"]}
        result = manifest_apply_manifest(["src/autoskillit/recipes/implementation.yaml"], manifest)
        assert result == {"recipe/"}

    def test_apply_manifest_doublestar_glob(self) -> None:
        manifest = {"docs/**/*.md": ["docs/"]}
        # Deep nested path
        result = manifest_apply_manifest(["docs/developer/README.md"], manifest)
        assert result == {"docs/"}
        # Zero intermediate segments
        result = manifest_apply_manifest(["docs/README.md"], manifest)
        assert result == {"docs/"}

    def test_apply_manifest_no_match_returns_none(self) -> None:
        manifest = {"src/autoskillit/recipes/*.yaml": ["recipe/"]}
        result = manifest_apply_manifest(["some/unknown/file.txt"], manifest)
        assert result is None

    def test_apply_manifest_multiple_files_union(self) -> None:
        manifest = {
            "src/autoskillit/recipes/*.yaml": ["recipe/"],
            "docs/**/*.md": ["docs/"],
        }
        result = manifest_apply_manifest(
            ["src/autoskillit/recipes/cook.yaml", "docs/guide.md"], manifest
        )
        assert result == {"recipe/", "docs/"}


# ---------------------------------------------------------------------------
# TestLoadCoverageMap
# ---------------------------------------------------------------------------


class TestLoadCoverageMap:
    def test_returns_dict_from_valid_json(self, tmp_path: Path) -> None:
        """Parses a valid JSON map and returns dict[str, set[str]]."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            '{"src/autoskillit/recipe/rules_dataflow.py": '
            '["tests/recipe/test_rules_dataflow.py", "tests/recipe/test_rules_structure.py"]}',
            encoding="utf-8",
        )
        result = load_coverage_map(map_file)
        assert result is not None
        assert "src/autoskillit/recipe/rules_dataflow.py" in result
        assert isinstance(result["src/autoskillit/recipe/rules_dataflow.py"], set)
        assert (
            "tests/recipe/test_rules_dataflow.py"
            in result["src/autoskillit/recipe/rules_dataflow.py"]
        )

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the map file does not exist."""
        result = load_coverage_map(tmp_path / "nonexistent.json")
        assert result is None

    def test_stale_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the file mtime is older than max_age_days."""
        import os
        import time

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text('{"src/foo.py": ["tests/test_foo.py"]}', encoding="utf-8")
        # Backdate mtime by 31 days
        old_mtime = time.time() - (31 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))
        result = load_coverage_map(map_file, max_age_days=30)
        assert result is None

    def test_fresh_file_returns_data(self, tmp_path: Path) -> None:
        """Returns data when the file mtime is within max_age_days."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text('{"src/foo.py": ["tests/test_foo.py"]}', encoding="utf-8")
        result = load_coverage_map(map_file, max_age_days=30)
        assert result is not None
        assert result["src/foo.py"] == {"tests/test_foo.py"}

    def test_custom_max_age_days_respected(self, tmp_path: Path) -> None:
        """Custom max_age_days threshold is respected."""
        import os
        import time

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text('{"src/foo.py": ["tests/test_foo.py"]}', encoding="utf-8")
        # Backdate by 2 days — stale with max_age_days=1, fresh with max_age_days=3
        old_mtime = time.time() - (2 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))
        assert load_coverage_map(map_file, max_age_days=1) is None
        assert load_coverage_map(map_file, max_age_days=3) is not None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """Returns None on JSON parse failure."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text("{bad json}", encoding="utf-8")
        result = load_coverage_map(map_file)
        assert result is None
