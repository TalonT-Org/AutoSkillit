"""Tests for scripts/compare-coverage-ast.py — AST extraction and coverage comparison."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "compare-coverage-ast.py"


@pytest.fixture(scope="module")
def cov_ast():
    """Import compare-coverage-ast.py as a module without polluting sys.path."""
    spec = importlib.util.spec_from_file_location("compare_coverage_ast", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


# ── T1a: extract_functions finds all definitions ──


def test_extract_functions_finds_all_definitions(cov_ast, tmp_path: Path):
    """extract_functions() returns FuncInfo for every def/async def."""
    src = tmp_path / "sample.py"
    src.write_text(
        """\
def top_level():
    pass

async def async_top():
    return 1

class Foo:
    def method(self):
        pass
""",
        encoding="utf-8",
    )
    funcs = cov_ast.extract_functions(src)
    names = [f.qualname for f in funcs]
    assert "top_level" in names
    assert "async_top" in names
    assert "Foo.method" in names
    async_func = next(f for f in funcs if f.qualname == "async_top")
    assert async_func.is_async is True


# ── T1b: nested classes ──


def test_extract_functions_handles_nested_classes(cov_ast, tmp_path: Path):
    """Nested class methods get qualified names like 'Outer.Inner.method'."""
    src = tmp_path / "nested.py"
    src.write_text(
        """\
class Outer:
    class Inner:
        def method(self):
            pass
""",
        encoding="utf-8",
    )
    funcs = cov_ast.extract_functions(src)
    qualnames = [f.qualname for f in funcs]
    assert "Outer.Inner.method" in qualnames


# ── T1c: syntax errors ──


def test_extract_functions_skips_syntax_errors(cov_ast, tmp_path: Path, capsys):
    """Files with syntax errors are skipped with a warning, not a crash."""
    src = tmp_path / "bad.py"
    src.write_text("def broken(\n", encoding="utf-8")
    funcs = cov_ast.extract_functions(src)
    assert funcs == []
    captured = capsys.readouterr()
    assert "SyntaxError" in captured.err


# ── T1d: uncovered functions ──


def test_compare_finds_uncovered_functions(cov_ast):
    """Functions whose line ranges have zero intersection with covered lines are reported."""
    func = cov_ast.FuncInfo(
        name="orphan",
        qualname="orphan",
        filepath="mod.py",
        lineno=10,
        end_lineno=20,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map: dict[str, set[int]] = {}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.uncovered == 1
    assert report.covered == 0


# ── T1e: covered functions ──


def test_compare_marks_covered_functions(cov_ast):
    """Functions whose line ranges overlap with covered lines are marked as covered."""
    func = cov_ast.FuncInfo(
        name="tested",
        qualname="tested",
        filepath="mod.py",
        lineno=1,
        end_lineno=3,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map = {"mod.py": {1, 2, 3}}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.covered == 1
    assert report.uncovered == 0


# ── T1f: partial coverage ──


def test_compare_detects_partial_coverage(cov_ast):
    """Functions where some lines are covered but not all are marked as partially covered."""
    func = cov_ast.FuncInfo(
        name="half",
        qualname="half",
        filepath="mod.py",
        lineno=1,
        end_lineno=10,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map = {"mod.py": {1, 2, 3}}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.partial == 1
    assert report.covered == 0
    assert report.uncovered == 0


# ── TestBuildTestSourceMap ──


class TestBuildTestSourceMap:
    def test_query_contexts_map_inverts_correctly(self, cov_ast, tmp_path, monkeypatch):
        """query_contexts_map inverts {line: {ctx}} to {src_file: {test_file}}.

        Uses MagicMock to simulate CoverageData.
        Context names use |run suffix.
        """
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")

        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_write|run"],
            2: ["tests/core/test_io.py::TestIO::test_write|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        result = cov_ast.query_contexts_map(tmp_path / ".coverage")
        assert "src/autoskillit/core/io.py" in result
        assert "tests/core/test_io.py" in result["src/autoskillit/core/io.py"]

    def test_setup_and_teardown_contexts_excluded(self, cov_ast, tmp_path, monkeypatch):
        """query_contexts_map excludes |setup and |teardown contexts.

        Only |run phase entries map source files to tests. A source file touched
        only during |setup or |teardown must NOT appear in the result.
        """
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")

        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: [
                "tests/core/test_io.py::TestIO::test_write|setup",
                "tests/core/test_io.py::TestIO::test_write|teardown",
            ],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        result = cov_ast.query_contexts_map(tmp_path / ".coverage")
        assert "src/autoskillit/core/io.py" not in result

    def test_build_test_source_map_writes_json(self, cov_ast, tmp_path, monkeypatch):
        """build_test_source_map() writes a valid JSON file to the output path."""
        import json
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")
        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_write|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / "test-source-map.json"
        cov_ast.build_test_source_map(db_path, output_path)

        assert output_path.exists()
        parsed = json.loads(output_path.read_text())
        assert isinstance(parsed, dict)
        expected_key = "src/autoskillit/core/io.py"
        assert expected_key in parsed
        assert "tests/core/test_io.py" in parsed[expected_key]

    def test_main_routes_build_test_source_map_mode(self, cov_ast, tmp_path, monkeypatch):
        """main() with --mode build-test-source-map calls build_test_source_map()."""

        called_with: dict = {}

        def fake_build(db_path, output_path):
            called_with["db_path"] = db_path
            called_with["output_path"] = output_path
            return True

        monkeypatch.setattr(cov_ast, "build_test_source_map", fake_build)
        monkeypatch.setattr(
            "sys.argv",
            ["compare-coverage-ast.py", "--mode", "build-test-source-map"],
        )
        result = cov_ast.main()
        assert result == 0
        assert "output_path" in called_with
        assert called_with["db_path"] == cov_ast.PROJECT_ROOT / ".coverage"

    def test_map_json_values_are_lists(self, cov_ast, tmp_path, monkeypatch):
        """The written JSON has list values (not sets), loadable as JSON."""
        import json
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")
        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_a|run"],
            2: ["tests/core/test_io.py::TestIO::test_b|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / "test-source-map.json"
        cov_ast.build_test_source_map(db_path, output_path)

        parsed = json.loads(output_path.read_text())
        for v in parsed.values():
            assert isinstance(v, list)

    def test_taskfile_coverage_audit_invokes_map_mode(self):
        """Taskfile.yml coverage-audit task includes --mode build-test-source-map."""
        taskfile = Path(__file__).parent.parent.parent / "Taskfile.yml"
        content = taskfile.read_text()
        assert "build-test-source-map" in content
