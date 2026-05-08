"""Tests for build_test_scope step 7 — aggressive mode file-level filtering (S11–S17)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from tests._test_filter import (
    FilterMode,
    build_test_scope,
)


class TestBuildTestScopeStep7:
    def test_step7_file_level_substitution_single_file(self, tmp_path: Path) -> None:
        """Coverage map entry replaces the cascade directory with specific test files."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        specific_test = tests_root / "core" / "test_io.py"
        specific_test.write_text("")

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            '{"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}',
            encoding="utf-8",
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
        )
        assert result is not None
        assert not any(p.is_dir() and p.name == "core" for p in result)
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
        map_file.write_text(
            '{"src/autoskillit/execution/headless.py": ["tests/execution/test_headless.py"]}',
            encoding="utf-8",
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
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

    def test_step7_stale_oracle_falls_back_to_directory(self, tmp_path: Path) -> None:
        """When load_coverage_map returns None (stale file), dir-level is preserved."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            '{"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}',
            encoding="utf-8",
        )
        old_mtime = time.time() - (31 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names

    def test_step7_conservative_mode_unaffected(self, tmp_path: Path) -> None:
        """Conservative mode is completely unaffected by step 7 and coverage_map_path."""
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
        map_file.write_text(
            '{"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}',
            encoding="utf-8",
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
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
        ]:
            assert expected in dir_names, f"conservative cascade lost {expected}"

    def test_step7_mixed_coverage_keeps_directory(self, tmp_path: Path) -> None:
        """If two src files map to same cascade dir and one lacks coverage data, dir is kept."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            '{"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}',
            encoding="utf-8",
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py", "src/autoskillit/core/logging.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
        )
        assert result is not None
        dir_names = {p.name for p in result if p.is_dir()}
        assert "core" in dir_names

    def test_step7_all_files_covered_replaces_directory(self, tmp_path: Path) -> None:
        """When all cascade-dir src files have oracle data, dir is replaced with specific files."""
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()
        (tests_root / "core" / "test_io.py").write_text("")
        (tests_root / "core" / "test_logging.py").write_text("")

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            '{"src/autoskillit/core/io.py": ["tests/core/test_io.py"],'
            ' "src/autoskillit/core/logging.py": ["tests/core/test_logging.py"]}',
            encoding="utf-8",
        )

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py", "src/autoskillit/core/logging.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
            coverage_map_path=map_file,
        )
        assert result is not None
        result_paths = list(result)
        assert not any(p.is_dir() and p.name == "core" for p in result_paths)
        names = {p.name for p in result_paths}
        assert "test_io.py" in names
        assert "test_logging.py" in names
