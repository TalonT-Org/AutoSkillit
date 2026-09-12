"""Tests for tests/_test_filter.py — standalone test-path filtering logic."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pathspec
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
    FullRunReason,
    ImportContext,
    _compile_manifest_matchers,
    apply_manifest,
    build_test_scope,
    check_bucket_a,
    git_changed_files,
    load_manifest,
)
from tests.conftest import TEST_TREE_LAYER_DIRS


def _make_tests_tree(tmp_path: Path) -> Path:
    """Build the standard temporary tests/ tree used by scope-building tests."""
    tests_root = tmp_path / "tests"
    for d in TEST_TREE_LAYER_DIRS:
        (tests_root / d).mkdir(parents=True, exist_ok=True)
    return tests_root


def _record_pathspec_construction(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Patch PathSpec.from_lines on the class and record its positional arguments."""
    calls: list[tuple] = []
    original = pathspec.PathSpec.from_lines

    def _recording_from_lines(*args: object, **kwargs: object) -> pathspec.PathSpec:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(pathspec.PathSpec, "from_lines", _recording_from_lines)
    return calls


pytestmark = [pytest.mark.medium]

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
    def test_scope_none_changed_returns_git_unavailable(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files=None,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is FullRunReason.GIT_UNAVAILABLE

    def test_scope_large_changeset_returns_full_run_reason(self, tmp_path: Path) -> None:
        files = {f"src/autoskillit/core/f{i}.py" for i in range(31)}
        result = build_test_scope(
            changed_files=files,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is FullRunReason.LARGE_CHANGESET

    def test_aggressive_mode_ignores_large_changeset_threshold(self, tmp_path: Path) -> None:
        """Aggressive mode does not trigger LARGE_CHANGESET even with >30 files."""
        tests_root = tmp_path / "tests"
        for d in ["core", "arch", "contracts"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        files = {f"src/autoskillit/core/f{i}.py" for i in range(35)}
        result = build_test_scope(
            changed_files=files,
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert isinstance(result, set), (
            f"Expected set[Path], got {type(result).__name__}: {result}"
        )

    def test_scope_bucket_a_returns_full_run_reason(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"pyproject.toml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is FullRunReason.BUCKET_A

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
        # 10 infra + 3 hooks unconditional files appear in result via direct_test_files
        result_names = {p.name for p in result}
        from tests._test_filter import _HOOKS_UNCONDITIONAL_FILES, _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        for fname in _HOOKS_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional hooks file {fname!r} missing"
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
        for test_path in [
            "infra/test_pretty_output_hook_infra.py",
            "arch/test_recipe_tracking_parity.py",
            "arch/test_recipe_enumeration_authority.py",
            "contracts/test_recipe_name_ledger.py",
        ]:
            path = tests_root / test_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        for expected in [
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
        ]:
            assert expected in result_names, f"{expected} missing from cascade"
        assert "test_pretty_output_hook_infra.py" in result_names, (
            "infra file missing from cascade"
        )
        for expected_path in [
            Path("tests/arch/test_recipe_tracking_parity.py"),
            Path("tests/arch/test_recipe_enumeration_authority.py"),
            Path("tests/contracts/test_recipe_name_ledger.py"),
        ]:
            target = tests_root.parent / expected_path
            assert any(selected == target or selected in target.parents for selected in result), (
                f"execution cascade missing {expected_path}"
            )
        assert "infra" not in result_names, "whole infra/ dir should not be in cascade"
        assert "skills" not in result_names, "skills/ dir should not be in execution layer cascade"

    def test_scope_l2_recipe_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["recipe", "arch", "contracts", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        for f in [
            "server/test_factory_context_construction.py",
            "server/test_tools_kitchen_envelope_failure.py",
            "server/test_tools_kitchen_envelope_hook_drift.py",
            "server/test_tools_kitchen_envelope_validation.py",
            "cli/test_cli_prompts.py",
            "cli/test_cook_order_picker.py",
            "execution/test_headless_path_validation.py",
            "execution/test_zero_write_detection.py",
            "migration/test_api.py",
            "migration/test_engine_recipe.py",
            "hooks/test_recipe_write_advisor.py",
            "hooks/test_recipe_contract_freshness.py",
            "infra/test_pretty_output_recipe.py",
            "skills/test_planner_skill_contracts.py",
            "skills/test_skill_placeholder_contracts.py",
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
            "test_factory_context_construction.py",
            "test_tools_kitchen_envelope_failure.py",
            "test_tools_kitchen_envelope_hook_drift.py",
            "test_tools_kitchen_envelope_validation.py",
            "test_cli_prompts.py",
            "test_headless_path_validation.py",
            "test_zero_write_detection.py",
            "test_pretty_output_recipe.py",
            "test_skill_placeholder_contracts.py",
            "test_recipe_contract_freshness.py",
            "core",
            "migration",
        ]:
            assert expected in result_names, f"{expected} missing"
        for absent in [
            "execution",
            "hooks",
            "infra",
            "skills",
            "server",
            "cli",
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
        from tests._test_filter import _HOOKS_UNCONDITIONAL_FILES, _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        for fname in _HOOKS_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional hooks file {fname!r} missing"
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
        """Non-Python file with manifest=None → fail-open → FullRunReason.UNMAPPED_FILE."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"README.md"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is FullRunReason.UNMAPPED_FILE

    def test_scope_nonpython_unmatched_manifest_full_run(self, tmp_path: Path) -> None:
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
        assert result is FullRunReason.UNMAPPED_FILE

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
        from tests._test_filter import _HOOKS_UNCONDITIONAL_FILES, _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        for fname in _HOOKS_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional hooks file {fname!r} missing"

    def test_scope_none_mode_returns_disabled(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.NONE,
            tests_root=tmp_path / "tests",
        )
        assert result is FullRunReason.DISABLED

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

    def test_scope_tests_claude_md_not_unmapped(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["core", "arch", "contracts"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        manifest = {"tests/**/CLAUDE.md": []}
        result = build_test_scope(
            changed_files={"tests/core/CLAUDE.md"},
            mode=FilterMode.CONSERVATIVE,
            manifest=manifest,
            tests_root=tests_root,
        )
        assert result is not None
        assert result is not FullRunReason.UNMAPPED_FILE

    # --- Manifest matcher reuse (T1) ---

    MANIFEST_A = {
        "docs/**/*.md": ["docs"],
        "*.yaml": ["config"],
        "scripts/*.py": ["cli"],
    }
    MANIFEST_B = {
        "docs/**/*.md": ["recipe"],
        "*.yaml": ["server"],
    }

    def test_manifest_matchers_compiled_once_per_invocation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each manifest pattern is compiled exactly once regardless of routed file count."""
        tests_root = _make_tests_tree(tmp_path)
        calls = _record_pathspec_construction(monkeypatch)

        result = build_test_scope(
            changed_files={"docs/a.md", "docs/deep/b.md", "settings.yaml", "scripts/run.py"},
            mode=FilterMode.CONSERVATIVE,
            manifest=self.MANIFEST_A,
            tests_root=tests_root,
        )

        assert len(calls) == len(self.MANIFEST_A), (
            f"expected {len(self.MANIFEST_A)} constructions, got {len(calls)}: {calls}"
        )
        assert calls == [("gitwildmatch", [pat]) for pat in self.MANIFEST_A]
        assert isinstance(result, set)
        dir_names = {p.name for p in result}
        assert {"docs", "config", "cli"} <= dir_names

    def test_source_only_scope_compiles_no_matchers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Source-only changes return before manifest routing, so nothing is compiled."""
        tests_root = _make_tests_tree(tmp_path)
        calls = _record_pathspec_construction(monkeypatch)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py", "tests/core/test_io.py"},
            mode=FilterMode.CONSERVATIVE,
            manifest=self.MANIFEST_A,
            tests_root=tests_root,
        )

        assert calls == []
        assert isinstance(result, set)

    def test_manifest_matchers_not_retained_across_invocations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second in-process call with a different manifest recompiles and reroutes."""
        tests_root = _make_tests_tree(tmp_path)
        calls = _record_pathspec_construction(monkeypatch)

        first = build_test_scope(
            changed_files={"docs/a.md"},
            mode=FilterMode.CONSERVATIVE,
            manifest=self.MANIFEST_A,
            tests_root=tests_root,
        )
        assert calls == [("gitwildmatch", [pat]) for pat in self.MANIFEST_A]
        calls.clear()

        second = build_test_scope(
            changed_files={"docs/a.md"},
            mode=FilterMode.CONSERVATIVE,
            manifest=self.MANIFEST_B,
            tests_root=tests_root,
        )

        assert calls == [("gitwildmatch", [pat]) for pat in self.MANIFEST_B]
        assert isinstance(first, set) and isinstance(second, set)
        assert "docs" in {p.name for p in first}
        assert "recipe" in {p.name for p in second}
        assert "recipe" not in {p.name for p in first}

    def test_manifest_construction_failure_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A matcher constructor failure at the first routed artifact propagates directly."""
        tests_root = _make_tests_tree(tmp_path)

        def _boom(*args: object, **kwargs: object) -> pathspec.PathSpec:
            raise RuntimeError("matcher construction failed")

        monkeypatch.setattr(pathspec.PathSpec, "from_lines", _boom)

        with pytest.raises(RuntimeError, match="matcher construction failed"):
            build_test_scope(
                changed_files={"docs/a.md"},
                mode=FilterMode.CONSERVATIVE,
                manifest=self.MANIFEST_A,
                tests_root=tests_root,
            )

    @pytest.mark.parametrize(
        "changed_files,expected",
        [
            ({"src/autoskillit/core/io.py"}, None),
            ({"tests/core/test_io.py"}, None),
            ({"pyproject.toml"}, FullRunReason.BUCKET_A),
        ],
        ids=["src_only", "test_only", "bucket_a"],
    )
    def test_paths_before_manifest_routing_never_construct_matchers(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        changed_files: set[str],
        expected: FullRunReason | None,
    ) -> None:
        """Source/test-only and earlier full-return paths never reach the constructor."""
        tests_root = _make_tests_tree(tmp_path)

        def _boom(*args: object, **kwargs: object) -> pathspec.PathSpec:
            raise RuntimeError("matcher construction failed")

        monkeypatch.setattr(pathspec.PathSpec, "from_lines", _boom)

        result = build_test_scope(
            changed_files=changed_files,
            mode=FilterMode.CONSERVATIVE,
            manifest=self.MANIFEST_A,
            tests_root=tests_root,
        )
        if expected is None:
            assert isinstance(result, set)
        else:
            assert result is expected


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

    @pytest.mark.parametrize("package", ["hooks", "server"])
    def test_output_budget_changes_cascade_to_codex_contracts(self, package: str) -> None:
        expected = {
            "execution/backends/test_codex_config.py",
            "execution/backends/test_codex_backend.py",
        }
        assert expected <= LAYER_CASCADE_CONSERVATIVE[package]


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
        assert first_call_args[3] == "origin/main"

    def test_git_changed_files_explicit_base_ref_is_not_prefixed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_BASE_REF", "main")

        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n"),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        git_changed_files("/fake", base_ref="main")
        first_call_args = list(mock_run.call_args_list[0][0][0])
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

    def test_production_manifest_guidance_routes_are_root_scoped(self) -> None:
        manifest = manifest_load_manifest(MANIFEST_PATH)
        nested_paths = tuple(
            f"{root}/{name}"
            for root in ("src/autoskillit/core", "tests/core")
            for name in ("AGENTS.md", "CLAUDE.md")
        )
        cases = {
            ("AGENTS.md", "CLAUDE.md"): {"infra/", "contracts/", "docs/"},
            (".github/AGENTS.md",): {"infra/"},
            ("docs/developer/contributing.md",): {"docs/", "infra/test_ci_workflow.py"},
            nested_paths: set(),
        }
        for paths, expected in cases.items():
            for path in paths:
                assert apply_manifest({path}, manifest) == expected
                assert manifest_apply_manifest([path], manifest) == expected

    # --- Semantics under both compilation paths (T2) ---

    @pytest.mark.parametrize(
        "manifest,changed_files,expected",
        [
            (
                {"docs/*.md": ["docs"], "docs/README.md": ["config"]},
                {"docs/README.md"},
                {"docs", "config"},
            ),
            ({"docs/**/*.md": ["docs"]}, {"docs/README.md"}, {"docs"}),
            ({"docs/**/*.md": ["docs"]}, {"docs/a/b/deep.md"}, {"docs"}),
            (
                {"tests/recipe/fixtures/*": ["recipe"]},
                {"tests/recipe/fixtures/sub/x.yaml"},
                {"recipe"},
            ),
            (
                {"docs/**/*.md": ["docs"], "*.yaml": ["config", "infra"]},
                {"docs/a.md", "settings.yaml"},
                {"docs", "config", "infra"},
            ),
            ({"docs/*.md": ["docs"]}, {"docs/a.md", "some/unknown/file.txt"}, None),
            (None, {"README.md"}, None),
            ({}, {"README.md"}, None),
            ({}, set(), set()),
            ({"*.yaml": "config"}, {"settings.yaml"}, {"config"}),
            ({"*.yaml": 42}, {"settings.yaml"}, set()),
        ],
        ids=[
            "overlapping_rules_union",
            "doublestar_zero_segments",
            "doublestar_nested_segments",
            "dir_star_matches_nested_file",
            "multiple_routed_files",
            "known_match_plus_unmapped_file",
            "manifest_none",
            "empty_manifest_nonempty_changed",
            "empty_manifest_empty_changed",
            "string_value_single_destination",
            "unsupported_value_matched_no_destination",
        ],
    )
    @pytest.mark.parametrize("supply_compiled", [False, True], ids=["compile", "supplied"])
    def test_apply_manifest_semantics(
        self,
        manifest: dict[str, object] | None,
        changed_files: set[str],
        expected: set[str] | None,
        supply_compiled: bool,
    ) -> None:
        """Ordinary and supplied-matcher calls agree with explicit expected results."""
        if supply_compiled and manifest is not None:
            compiled = _compile_manifest_matchers(manifest)
            result = apply_manifest(changed_files, manifest, compiled_matchers=compiled)
        else:
            result = apply_manifest(changed_files, manifest)
        assert result == expected

    def test_compile_manifest_matchers_uses_gitwildmatch_per_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One gitwildmatch matcher is built per manifest key, in manifest order."""
        manifest = {"docs/**/*.md": ["docs"], "*.yaml": ["config"]}
        calls = _record_pathspec_construction(monkeypatch)

        compiled = _compile_manifest_matchers(manifest)

        assert calls == [("gitwildmatch", [pat]) for pat in manifest]
        assert set(compiled) == set(manifest)
        assert all(isinstance(v, pathspec.PathSpec) for v in compiled.values())

    def test_supplied_matchers_avoid_recompilation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A supplied matcher dictionary suppresses all further construction."""
        manifest = {"docs/**/*.md": ["docs"], "*.yaml": ["config"]}
        compiled = _compile_manifest_matchers(manifest)
        calls = _record_pathspec_construction(monkeypatch)

        result = apply_manifest({"docs/a.md"}, manifest, compiled_matchers=compiled)

        assert calls == []
        assert result == {"docs"}

    def test_empty_supplied_matchers_is_not_treated_as_absent(self) -> None:
        """An empty supplied dictionary is used as given rather than recompiled."""
        manifest = {"docs/**/*.md": ["docs"]}
        assert apply_manifest({"docs/a.md"}, manifest, compiled_matchers={}) is None

    def test_apply_manifest_construction_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direct calls propagate constructor failures rather than failing open."""

        def _boom(*args: object, **kwargs: object) -> pathspec.PathSpec:
            raise RuntimeError("matcher construction failed")

        monkeypatch.setattr(pathspec.PathSpec, "from_lines", _boom)

        with pytest.raises(RuntimeError, match="matcher construction failed"):
            apply_manifest({"docs/a.md"}, {"docs/*.md": ["docs"]})


_CONTRIBUTING_DOC_TOKEN_RE = re.compile(r"""(['"])contributing\.md\1""")


def _contributing_document_scope() -> set[Path]:
    """Conservative scope for a change to docs/developer/contributing.md against the
    real manifest and the real tests/ tree."""
    scope = build_test_scope(
        changed_files={"docs/developer/contributing.md"},
        mode=FilterMode.CONSERVATIVE,
        manifest=load_manifest(PROJECT_ROOT),
        tests_root=PROJECT_ROOT / "tests",
    )
    assert isinstance(scope, set), f"expected a path scope, got {scope!r}"
    return scope


class TestProductionManifestScope:
    """Resulting-scope calibration against the real manifest and the real tests/ tree."""

    def test_contributing_document_route_selects_only_its_infra_reader(self) -> None:
        from tests._test_filter import _HOOKS_UNCONDITIONAL_FILES, _INFRA_UNCONDITIONAL_FILES

        tests_root = PROJECT_ROOT / "tests"
        scope = _contributing_document_scope()

        infra_dir = tests_root / "infra"
        ci_reader = infra_dir / "test_ci_workflow.py"
        guard_paths = {infra_dir / name for name in _INFRA_UNCONDITIONAL_FILES} | {
            tests_root / "hooks" / name for name in _HOOKS_UNCONDITIONAL_FILES
        }

        assert {tests_root / "docs", tests_root / "arch", tests_root / "contracts"} <= scope
        assert ci_reader in scope
        assert guard_paths <= scope

        assert infra_dir not in scope, "docs change must not broaden to all of tests/infra"
        infra_entries = {p for p in scope if p.is_relative_to(infra_dir)}
        assert infra_entries == {ci_reader} | {p for p in guard_paths if p.parent == infra_dir}
        assert infra_dir / "test_pretty_output_recipe.py" not in scope

    def test_contributing_document_route_file_targets_read_the_document(self) -> None:
        route = load_manifest(PROJECT_ROOT)["docs/developer/contributing.md"]
        file_targets = [target for target in route if not target.endswith("/")]
        assert file_targets, "route must name at least one file-level reader"
        for target in file_targets:
            source = (PROJECT_ROOT / "tests" / target).read_text(encoding="utf-8")
            assert _CONTRIBUTING_DOC_TOKEN_RE.search(source), (
                f"{target!r} is routed as a reader of docs/developer/contributing.md "
                "but never references the document"
            )

    def test_every_infra_module_referencing_the_document_stays_selected(self) -> None:
        scope = _contributing_document_scope()
        infra_dir = PROJECT_ROOT / "tests" / "infra"
        referencing = {
            module
            for module in infra_dir.rglob("test_*.py")
            if _CONTRIBUTING_DOC_TOKEN_RE.search(module.read_text(encoding="utf-8"))
        }
        assert infra_dir / "test_ci_workflow.py" in referencing

        unselected = sorted(
            module.relative_to(PROJECT_ROOT).as_posix()
            for module in referencing
            if module not in scope and not any(module.is_relative_to(d) for d in scope)
        )
        assert not unselected, (
            "tests/infra modules reference docs/developer/contributing.md but are not "
            "selected for a change to it; add each as a file-level target of the "
            "docs/developer/contributing.md route, or drop the reference if the module "
            f"does not read the document: {unselected}"
        )


# ---------------------------------------------------------------------------
# Behavioral equivalence cross-validation (EQ1–EQ2)
# ---------------------------------------------------------------------------


class TestApplyManifestEquivalence:
    """Cross-validates the test and production manifest implementations."""

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
        non_empty_count = 0
        for pattern, dirs in manifest.items():
            assert isinstance(dirs, list)
            assert all(isinstance(d, str) for d in dirs)
            if len(dirs) > 0:
                non_empty_count += 1
        assert non_empty_count >= 22

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

    def test_apply_manifest_sub_recipes_yaml(self) -> None:
        manifest = manifest_load_manifest(MANIFEST_PATH)
        assert manifest is not None
        result = manifest_apply_manifest(
            ["src/autoskillit/recipes/sub-recipes/research.yaml"],
            manifest,
        )
        assert result == {"recipe/"}
