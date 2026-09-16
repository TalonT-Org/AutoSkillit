"""Tests for build_test_scope step 7 — coverage oracle file-level filtering (S11–S17)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests import _test_filter as test_filter
from tests._test_filter import (
    FilterMode,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]

SOURCE_COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def accept_source_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        test_filter.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )


def _write_successful_source_map(path: Path, source_map: dict[str, list[str]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provenance": {"pytest_exit_code": 0, "source_commit": SOURCE_COMMIT},
                "map": source_map,
            }
        ),
        encoding="utf-8",
    )


class TestBuildTestScopeStep7:
    def test_step7_narrowing_requires_successful_provenance(self, tmp_path: Path) -> None:
        """Only a successful producer status authorizes map-based test augmentation."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()
        specific_test = tests_root / "core" / "test_io.py"
        specific_test.write_text("")

        source_map = {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}
        success_map = tmp_path / "success-source-map.json"
        failed_map = tmp_path / "failed-source-map.json"
        for path, pytest_exit_code in ((success_map, 0), (failed_map, 1)):
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provenance": {
                            "pytest_exit_code": pytest_exit_code,
                            "source_commit": SOURCE_COMMIT,
                        },
                        "map": source_map,
                    }
                ),
                encoding="utf-8",
            )

        narrowed = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=success_map,
            cwd=tmp_path,
        )
        fallback = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=failed_map,
            cwd=tmp_path,
        )

        assert narrowed is not None
        assert fallback is not None
        assert specific_test in narrowed
        assert specific_test not in fallback
        assert any(path.is_dir() and path.name == "core" for path in narrowed)
        assert any(path.is_dir() and path.name == "core" for path in fallback)

    def test_step7_file_level_substitution_single_file(self, tmp_path: Path) -> None:
        """Coverage map entry adds specific test files without removing the cascade directory."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        specific_test = tests_root / "core" / "test_io.py"
        specific_test.write_text("")

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]},
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        assert any(p.is_dir() and p.name == "core" for p in result)
        paths = {str(p) for p in result}
        assert any("test_io.py" in p for p in paths)
        assert any("arch" in p for p in paths)
        assert any("contracts" in p for p in paths)

    def test_step7_fallback_to_dir_when_file_not_in_map(self, tmp_path: Path) -> None:
        """When src file has no coverage map entry, directory-level entry is preserved."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/execution/headless.py": ["tests/execution/test_headless.py"]},
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names

    def test_step7_no_path_uses_directory_level(self, tmp_path: Path) -> None:
        """When coverage_map_path is None, step 7 is skipped entirely."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=None,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names

    def test_step7_requires_cwd_to_load_oracle(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for directory in ("core", "arch", "contracts"):
            (tests_root / directory).mkdir(parents=True)
        (tests_root / "core" / "test_io.py").write_text("")
        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file, {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
        )

        assert result is not None
        assert tests_root / "core" in result
        assert tests_root / "core" / "test_io.py" not in result

    def test_scoped_conftest_and_arch_helper_survive_refinement(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        helper_dirs = {
            "arch",
            "contracts",
            "execution",
            "recipe/rules_skills",
            "skills",
            "workspace",
        }
        for directory in {"core", *helper_dirs}:
            (tests_root / directory).mkdir(parents=True)
        specific_test = tests_root / "core" / "test_io.py"
        specific_test.write_text("")
        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file, {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}
        )

        result = build_test_scope(
            changed_files={
                "src/autoskillit/core/io.py",
                "tests/core/conftest.py",
                "tests/arch/_helpers.py",
            },
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )

        assert result is not None
        assert specific_test in result
        assert tests_root / "core" in result
        assert {tests_root / directory for directory in helper_dirs} <= result

    def test_step7_stale_oracle_falls_back_to_directory(self, tmp_path: Path) -> None:
        """When load_coverage_map returns None (stale file), dir-level is preserved."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]},
        )
        old_mtime = time.time() - (31 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names

    def test_step7_non_ancestor_oracle_falls_back_to_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tests_root = tmp_path / "tests"
        for directory in ("core", "arch", "contracts"):
            (tests_root / directory).mkdir(parents=True)
        (tests_root / "core" / "test_io.py").write_text("")
        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file, {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}
        )
        monkeypatch.setattr(
            test_filter.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 1),
        )

        with pytest.warns(UserWarning, match="not an ancestor of HEAD"):
            result = build_test_scope(
                changed_files={"src/autoskillit/core/io.py"},
                mode=FilterMode.AGGRESSIVE,
                tests_root=tests_root,
                coverage_map_path=map_file,
                cwd=tmp_path,
            )

        assert result is not None
        assert tests_root / "core" in result

    def test_step7_conservative_mode_augments_without_narrowing_any_package(
        self, tmp_path: Path
    ) -> None:
        """Conservative mode adds oracle file targets without narrowing any cascade dir."""
        tests_root = tmp_path / "tests"
        for d in [
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
            "arch",
            "contracts",
            "infra",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        specific_test = tests_root / "core" / "test_io.py"
        specific_test.write_text("")

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]},
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names
        paths = {str(p) for p in result}
        assert any("test_io.py" in p for p in paths)
        for expected in [
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "fleet",
            "server",
            "cli",
        ]:
            assert expected in dir_names, f"conservative cascade lost {expected}"

    def test_step7_conservative_no_oracle_keeps_all_dirs(self, tmp_path: Path) -> None:
        """Without oracle, conservative mode keeps all cascade dirs including same-package."""
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
            coverage_map_path=None,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
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
        ]:
            assert expected in dir_names, f"conservative cascade lost {expected}"

    def test_step7_conservative_stale_oracle_keeps_all_dirs(self, tmp_path: Path) -> None:
        """Stale oracle in conservative mode falls back to full directory-level cascade."""
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

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]},
        )
        old_mtime = time.time() - (31 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
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
        ]:
            assert expected in dir_names, f"stale-oracle fallback lost {expected}"

    def test_step7_mixed_coverage_keeps_directory(self, tmp_path: Path) -> None:
        """If two src files map to same cascade dir and one lacks coverage data, dir is kept."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]},
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py", "src/autoskillit/core/logging.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names

    def test_step7_all_files_covered_keeps_directory(self, tmp_path: Path) -> None:
        """When all cascade-dir src files have oracle data, files are added; dir stays."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()
        (tests_root / "core" / "test_io.py").write_text("")
        (tests_root / "core" / "test_logging.py").write_text("")

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {
                "src/autoskillit/core/io.py": ["tests/core/test_io.py"],
                "src/autoskillit/core/logging.py": ["tests/core/test_logging.py"],
            },
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py", "src/autoskillit/core/logging.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        result_paths = list(result)
        assert any(p.is_dir() and p.name == "core" for p in result_paths)
        names = {p.name for p in result_paths}
        assert "test_io.py" in names
        assert "test_logging.py" in names

    def test_step7_incomplete_map_entry_does_not_drop_untracked_test(self, tmp_path: Path) -> None:
        """Regression for #5032: a present-but-incomplete entry must augment, not narrow."""
        tests_root = tmp_path / "tests"
        (tests_root / "hooks").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()
        (tests_root / "hooks" / "test_hook_registry.py").write_text("")
        untracked_test = tests_root / "hooks" / "test_ingredient_lock_guard.py"
        untracked_test.write_text("")

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {
                "src/autoskillit/hooks/guards/ingredient_lock_guard.py": [
                    "tests/hooks/test_hook_registry.py"
                ]
            },
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/hooks/guards/ingredient_lock_guard.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        assert tests_root / "hooks" in result

    @pytest.mark.parametrize(
        "package_name,cascade_targets",
        sorted(
            (name, targets)
            for name, targets in test_filter.LAYER_CASCADE_AGGRESSIVE.items()
            if len(targets) > 1
        ),
    )
    def test_step7_multi_target_cascade_survives_oracle_augmentation(
        self, tmp_path: Path, package_name: str, cascade_targets: frozenset[str]
    ) -> None:
        """Named regression record for the multi-target LAYER_CASCADE_AGGRESSIVE entries.

        Step 7 no longer reads LAYER_CASCADE_AGGRESSIVE (2.2) — this parametrization is
        source-derived so the record stays accurate, but it is not a live coupling and
        does not automatically protect a future table entry.
        """
        tests_root = tmp_path / "tests"
        for target in cascade_targets:
            target_path = tests_root / target
            if target.endswith(".py"):
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text("")
            else:
                target_path.mkdir(parents=True, exist_ok=True)
        (tests_root / "arch").mkdir(exist_ok=True)
        (tests_root / "contracts").mkdir(exist_ok=True)
        (tests_root / "arch" / "test_dummy.py").write_text("")

        changed_file = f"src/autoskillit/{package_name}.py"
        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(map_file, {changed_file: ["tests/arch/test_dummy.py"]})

        result = build_test_scope(
            changed_files={changed_file},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        for target in cascade_targets:
            assert tests_root / target in result, f"{package_name} lost cascade target {target}"

    def test_step7_oracle_does_not_defeat_always_run_unconditional_tier(
        self, tmp_path: Path
    ) -> None:
        """REQ-TIER-001: oracle augmentation must not defeat the arch/contracts tier."""
        tests_root = tmp_path / "tests"
        (tests_root / "arch").mkdir(parents=True)
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        _write_successful_source_map(
            map_file,
            {
                "src/autoskillit/_test_filter.py": [
                    "tests/test_test_filter_step7.py",
                    "tests/test_test_filter_coverage_map.py",
                    "tests/test_test_filter_core_cascade.py",
                ]
            },
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/_test_filter.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "arch" in dir_names
        assert "contracts" in dir_names
