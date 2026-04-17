#!/usr/bin/env python3
"""Compare AST-derived function map against pytest-cov coverage database.

Walks all Python files under a source root, extracts every function/method
definition via AST, then queries the .coverage SQLite database (via the
coverage.py public API) to determine which functions have test coverage.

Inputs:
    --coverage-db  Path to .coverage file (default: .coverage in project root)
    --src-root     Source directory to scan (default: src/autoskillit)
    --output       Optional JSON report output path

Exit codes:
    0  Always (audit tool, not a gate)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FuncInfo:
    name: str
    qualname: str
    filepath: str
    lineno: int
    end_lineno: int
    is_async: bool


@dataclass
class FuncCoverage:
    func: FuncInfo
    total_lines: int
    covered_lines: int
    status: str  # "covered", "partial", "uncovered"


@dataclass
class Report:
    total: int = 0
    covered: int = 0
    partial: int = 0
    uncovered: int = 0
    details: dict[str, list[FuncCoverage]] = field(default_factory=dict)


class _FuncVisitor(ast.NodeVisitor):
    """AST visitor that tracks class/function nesting to build qualified names."""

    def __init__(self, filepath: str) -> None:
        self._filepath = filepath
        self._name_stack: list[str] = []
        self.functions: list[FuncInfo] = []

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._name_stack, node.name])
        start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        end = node.end_lineno or node.lineno
        self.functions.append(
            FuncInfo(
                name=node.name,
                qualname=qualname,
                filepath=self._filepath,
                lineno=start,
                end_lineno=end,
                is_async=isinstance(node, ast.AsyncFunctionDef),
            )
        )
        self._name_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._name_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._name_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._name_stack.pop()


def extract_functions(source_path: Path, filepath: str = "") -> list[FuncInfo]:
    """Parse a Python file and return FuncInfo for every function/method.

    Args:
        source_path: Absolute path to the Python file on disk.
        filepath: Value to store in FuncInfo.filepath (e.g. a relative path).
                  Defaults to str(source_path) when empty.
    """
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except SyntaxError:
        print(f"WARNING: Skipping {source_path} (SyntaxError)", file=sys.stderr)
        return []
    visitor = _FuncVisitor(filepath or str(source_path))
    visitor.visit(tree)
    return visitor.functions


def build_ast_map(src_root: Path) -> dict[str, list[FuncInfo]]:
    """Walk source root and build {relative_path: [FuncInfo]} map."""
    result: dict[str, list[FuncInfo]] = {}
    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(PROJECT_ROOT))
        funcs = extract_functions(py_file, filepath=rel)
        if funcs:
            result[rel] = funcs
    return result


def query_coverage_db(
    db_path: Path,
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Query .coverage database and return (covered_map, executable_map).

    covered_map:    {relative_path: {covered_line_numbers}}
    executable_map: {relative_path: {all_executable_line_numbers}}

    Uses coverage.Coverage.analysis2() to obtain the full set of executable
    lines (statements) per file, not just the executed subset.
    """
    import coverage

    try:
        cov = coverage.Coverage(data_file=str(db_path))
        cov.load()
    except Exception as exc:
        print(
            f"ERROR: Failed to read coverage database {db_path}: {exc}",
            file=sys.stderr,
        )
        return {}, {}
    covered_result: dict[str, set[int]] = {}
    executable_result: dict[str, set[int]] = {}
    for measured_file in cov.get_data().measured_files():
        try:
            rel = str(Path(measured_file).relative_to(PROJECT_ROOT))
        except ValueError:
            continue
        try:
            analysis = cov.analysis2(measured_file)
        except Exception:
            continue
        statements = set(analysis[1])
        missing = set(analysis[3])
        covered = statements - missing
        if covered:
            covered_result[rel] = covered
        if statements:
            executable_result[rel] = statements
    return covered_result, executable_result


def query_contexts_map(db_path: Path) -> dict[str, set[str]]:
    """Query .coverage DB and return {source_file: {test_file_paths}}.

    Uses CoverageData.contexts_by_lineno() to build the inversion.
    Only includes source files under src/ and test contexts from tests/.
    Context names from --cov-context=test are test node IDs like
    'tests/recipe/test_rules_dataflow.py::TestClass::test_method|run'.
    Only |run phase contexts are included to exclude fixture-inflation from
    |setup and |teardown phases.
    """
    import coverage

    try:
        data = coverage.CoverageData(basename=str(db_path))
        data.read()
    except Exception as exc:
        print(f"ERROR: Failed to read coverage database {db_path}: {exc}", file=sys.stderr)
        return {}

    result: dict[str, set[str]] = {}
    for measured_file in data.measured_files():
        try:
            rel = str(Path(measured_file).relative_to(PROJECT_ROOT))
        except ValueError:
            continue
        if not rel.startswith("src/"):
            continue
        contexts_by_line = data.contexts_by_lineno(measured_file)
        test_files: set[str] = set()
        for contexts in contexts_by_line.values():
            for ctx in contexts:
                if "::" in ctx and ctx.endswith("|run"):
                    test_file = ctx.split("::")[0]
                    if test_file.startswith("tests/") and test_file.endswith(".py"):
                        test_files.add(test_file)
        if test_files:
            result[rel] = test_files
    return result


def build_test_source_map(
    db_path: Path,
    output_path: Path,
) -> bool:
    """Build and write {source_file: [test_files]} map from coverage DB.

    Args:
        db_path: Path to .coverage SQLite database.
        output_path: Path where test-source-map.json will be written.

    Returns:
        True on success, False when the coverage DB is not found.
    """
    if not db_path.exists():
        print(f"WARNING: Coverage database not found: {db_path}", file=sys.stderr)
        print("Run 'task coverage-audit' first to generate coverage data.", file=sys.stderr)
        return False

    mapping = query_contexts_map(db_path)
    # Convert sets to sorted lists for stable, human-readable JSON
    serializable = {src: sorted(tests) for src, tests in sorted(mapping.items())}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    print(f"Test-source map written to: {output_path} ({len(serializable)} source files)")
    return True


def compare(
    ast_map: dict[str, list[FuncInfo]],
    coverage_map: dict[str, set[int]],
    executable_map: dict[str, set[int]] | None = None,
) -> Report:
    """Compare AST function ranges against coverage data."""
    report = Report()
    for filepath, funcs in sorted(ast_map.items()):
        covered_lines = coverage_map.get(filepath, set())
        executable_lines = executable_map.get(filepath, set()) if executable_map else set()
        file_details: list[FuncCoverage] = []
        for func in funcs:
            func_lines = set(range(func.lineno, func.end_lineno + 1))
            # Use executable lines within the function range when available,
            # otherwise fall back to the full AST line range.
            if executable_lines:
                func_executable = func_lines & executable_lines
            else:
                func_executable = func_lines
            intersection = func_executable & covered_lines
            total = len(func_executable) if func_executable else len(func_lines)
            covered_count = len(intersection)

            if covered_count == 0:
                status = "uncovered"
                report.uncovered += 1
            elif covered_count >= total:
                status = "covered"
                report.covered += 1
            else:
                status = "partial"
                report.partial += 1

            report.total += 1
            file_details.append(
                FuncCoverage(
                    func=func,
                    total_lines=total,
                    covered_lines=covered_count,
                    status=status,
                )
            )
        if file_details:
            report.details[filepath] = file_details
    return report


def _print_report(report: Report) -> None:
    """Print human-readable summary to stdout."""
    print()
    print("Coverage Audit Report")
    print("=====================")
    if report.total == 0:
        print("No functions found.")
        return

    pct_c = round(report.covered * 100 / report.total)
    pct_p = round(report.partial * 100 / report.total)
    pct_u = round(report.uncovered * 100 / report.total)
    print(f"Total functions:     {report.total}")
    print(f"Fully covered:       {report.covered} ({pct_c}%)")
    print(f"Partially covered:   {report.partial} ({pct_p}%)")
    print(f"Uncovered:           {report.uncovered} ({pct_u}%)")

    uncovered_entries = {
        fp: [fc for fc in fcs if fc.status == "uncovered"] for fp, fcs in report.details.items()
    }
    uncovered_entries = {fp: fcs for fp, fcs in uncovered_entries.items() if fcs}

    if uncovered_entries:
        print()
        print("BLIND SPOTS (uncovered functions):")
        print("\u2500" * 34)
        for filepath, fcs in sorted(uncovered_entries.items()):
            print(f"  {filepath}:")
            for fc in fcs:
                print(f"    L{fc.func.lineno}-L{fc.func.end_lineno}  {fc.func.qualname}")


def _write_json(report: Report, output_path: Path) -> None:
    """Write structured JSON report."""
    data = {
        "total": report.total,
        "covered": report.covered,
        "partial": report.partial,
        "uncovered": report.uncovered,
        "details": {fp: [asdict(fc) for fc in fcs] for fp, fcs in report.details.items()},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"\nJSON report written to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare AST function map against coverage database"
    )
    parser.add_argument(
        "--mode",
        choices=["audit", "build-test-source-map"],
        default="audit",
        help="Operation mode (default: audit)",
    )
    parser.add_argument(
        "--coverage-db",
        type=Path,
        default=PROJECT_ROOT / ".coverage",
        help="Path to .coverage database (default: .coverage in project root)",
    )
    parser.add_argument(
        "--src-root",
        type=Path,
        default=PROJECT_ROOT / "src" / "autoskillit",
        help="Source directory to scan (default: src/autoskillit)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for JSON report output (audit mode) or map output (build-test-source-map mode)",
    )
    args = parser.parse_args()

    if args.mode == "build-test-source-map":
        output_path = args.output or (PROJECT_ROOT / ".autoskillit" / "test-source-map.json")
        ok = build_test_source_map(args.coverage_db, output_path)
        return 0 if ok else 1

    if not args.src_root.is_dir():
        print(f"ERROR: Source root not found: {args.src_root}", file=sys.stderr)
        return 0

    ast_map = build_ast_map(args.src_root)
    if not args.coverage_db.exists():
        print(f"WARNING: Coverage database not found: {args.coverage_db}", file=sys.stderr)
        print("Run pytest with --cov first to generate coverage data.", file=sys.stderr)
        total_funcs = sum(len(v) for v in ast_map.values())
        print(
            f"\nNo coverage data available ({total_funcs} functions discovered, "
            "coverage status unknown)."
        )
        return 0

    coverage_map, executable_map = query_coverage_db(args.coverage_db)
    report = compare(ast_map, coverage_map, executable_map)
    _print_report(report)

    if args.output:
        _write_json(report, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
