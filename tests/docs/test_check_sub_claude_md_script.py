"""Unit and integration tests for scripts/check_sub_claude_md.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "check_sub_claude_md.py"


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("check_sub_claude_md", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


class TestCheckCoverage:
    def test_check_coverage_all_files_mentioned(self, script_mod, tmp_path):
        """Returns empty list when CLAUDE.md mentions every .py file in the directory."""
        subdir = tmp_path / "mypackage"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text(
            "| File | Purpose |\n|------|----------|\n"
            "| `__init__.py` | init |\n| `foo.py` | foo |\n",
            encoding="utf-8",
        )
        (subdir / "__init__.py").touch()
        (subdir / "foo.py").touch()
        result = script_mod.check_coverage(tmp_path, ["mypackage/CLAUDE.md"])
        assert result == []

    def test_check_coverage_missing_regular_py_file(self, script_mod, tmp_path):
        """Returns failure string containing the missing filename."""
        subdir = tmp_path / "mypackage"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text(
            "| File | Purpose |\n|------|----------|\n| `__init__.py` | init |\n",
            encoding="utf-8",
        )
        (subdir / "__init__.py").touch()
        (subdir / "bar.py").touch()
        result = script_mod.check_coverage(tmp_path, ["mypackage/CLAUDE.md"])
        assert result == ["mypackage/CLAUDE.md: missing bar.py"]

    def test_check_coverage_missing_init_py_backtick(self, script_mod, tmp_path):
        """Returns failure when __init__.py exists but CLAUDE.md lacks backtick-wrapped mention."""
        subdir = tmp_path / "mypackage"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text(
            "| File | Purpose |\n|------|----------|\n| `foo.py` | foo |\n",
            encoding="utf-8",
        )
        (subdir / "__init__.py").touch()
        (subdir / "foo.py").touch()
        result = script_mod.check_coverage(tmp_path, ["mypackage/CLAUDE.md"])
        assert result == ["mypackage/CLAUDE.md: missing `__init__.py` in file table"]

    def test_check_coverage_init_py_without_backticks_fails(self, script_mod, tmp_path):
        """Returns failure when CLAUDE.md mentions __init__.py without backtick wrapping."""
        subdir = tmp_path / "mypackage"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text(
            "| File | Purpose |\n|------|----------|\n| __init__.py | init |\n",
            encoding="utf-8",
        )
        (subdir / "__init__.py").touch()
        result = script_mod.check_coverage(tmp_path, ["mypackage/CLAUDE.md"])
        assert result == ["mypackage/CLAUDE.md: missing `__init__.py` in file table"]

    def test_check_coverage_flags_missing_claude_md(self, script_mod, tmp_path):
        """Returns failure when expected CLAUDE.md path does not exist on disk."""
        result = script_mod.check_coverage(tmp_path, ["nonexistent/CLAUDE.md"])
        assert result == ["nonexistent/CLAUDE.md: CLAUDE.md not found"]

    def test_check_coverage_multiple_missing_files(self, script_mod, tmp_path):
        """Returns one failure entry per missing file."""
        subdir = tmp_path / "mypackage"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text(
            "| File | Purpose |\n|------|----------|\n",
            encoding="utf-8",
        )
        (subdir / "__init__.py").touch()
        (subdir / "a.py").touch()
        (subdir / "b.py").touch()
        result = sorted(script_mod.check_coverage(tmp_path, ["mypackage/CLAUDE.md"]))
        assert result == [
            "mypackage/CLAUDE.md: missing `__init__.py` in file table",
            "mypackage/CLAUDE.md: missing a.py",
            "mypackage/CLAUDE.md: missing b.py",
        ]


class TestExpectedListsSync:
    def test_src_expected_matches_test_file(self, script_mod):
        """SRC_EXPECTED matches EXPECTED_SUB_CLAUDE_MDS in test_sub_claude_md_completeness."""
        test_module_name = "test_sub_claude_md_completeness"
        test_file = REPO_ROOT / "tests" / "docs" / f"{test_module_name}.py"
        spec = importlib.util.spec_from_file_location(test_module_name, test_file)
        assert spec is not None and spec.loader is not None
        test_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(test_mod)
        assert sorted(script_mod.SRC_EXPECTED) == sorted(test_mod.EXPECTED_SUB_CLAUDE_MDS)

    def test_tests_expected_matches_test_file(self, script_mod):
        """TESTS_EXPECTED list matches that in test_tests_sub_claude_md_completeness."""
        test_module_name = "test_tests_sub_claude_md_completeness"
        test_file = REPO_ROOT / "tests" / "docs" / f"{test_module_name}.py"
        spec = importlib.util.spec_from_file_location(test_module_name, test_file)
        assert spec is not None and spec.loader is not None
        test_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(test_mod)
        assert sorted(script_mod.TESTS_EXPECTED) == sorted(test_mod.EXPECTED_SUB_CLAUDE_MDS)


class TestMain:
    def test_main_returns_zero_on_live_repo(self, script_mod):
        """main() returns 0 against the actual project (integration guard)."""
        result = script_mod.main()
        assert result == 0, "main() should return 0 on a clean repo"
