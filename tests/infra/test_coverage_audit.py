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
