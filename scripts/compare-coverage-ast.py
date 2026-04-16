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
        self.generic_visit(node)
        self._name_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._name_stack.append(node.name)
        self.generic_visit(node)
        self._name_stack.pop()


def extract_functions(source_path: Path) -> list[FuncInfo]:
    """Parse a Python file and return FuncInfo for every function/method."""
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except SyntaxError:
        print(f"WARNING: Skipping {source_path} (SyntaxError)", file=sys.stderr)
        return []
    visitor = _FuncVisitor(str(source_path))
    visitor.visit(tree)
    return visitor.functions


def build_ast_map(src_root: Path) -> dict[str, list[FuncInfo]]:
    """Walk source root and build {relative_path: [FuncInfo]} map."""
    result: dict[str, list[FuncInfo]] = {}
    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(PROJECT_ROOT))
        funcs = extract_functions(py_file)
        if funcs:
            for f in funcs:
                f.filepath = rel
            result[rel] = funcs
    return result


def query_coverage_db(db_path: Path) -> dict[str, set[int]]:
    """Query .coverage database and return {relative_path: {covered_lines}}."""
    from coverage import CoverageData

    data = CoverageData(str(db_path))
    data.read()
    result: dict[str, set[int]] = {}
    for measured_file in data.measured_files():
        lines = data.lines(measured_file)
        if lines:
            try:
                rel = str(Path(measured_file).relative_to(PROJECT_ROOT))
            except ValueError:
                continue
            result[rel] = set(lines)
    return result


def compare(
    ast_map: dict[str, list[FuncInfo]], coverage_map: dict[str, set[int]]
) -> Report:
    """Compare AST function ranges against coverage data."""
    report = Report()
    for filepath, funcs in sorted(ast_map.items()):
        covered_lines = coverage_map.get(filepath, set())
        file_details: list[FuncCoverage] = []
        for func in funcs:
            func_lines = set(range(func.lineno, func.end_lineno + 1))
            intersection = func_lines & covered_lines
            total = len(func_lines)
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

    pct_c = report.covered * 100 // report.total
    pct_p = report.partial * 100 // report.total
    pct_u = report.uncovered * 100 // report.total
    print(f"Total functions:     {report.total}")
    print(f"Fully covered:       {report.covered} ({pct_c}%)")
    print(f"Partially covered:   {report.partial} ({pct_p}%)")
    print(f"Uncovered:           {report.uncovered} ({pct_u}%)")

    uncovered_entries = {
        fp: [fc for fc in fcs if fc.status == "uncovered"]
        for fp, fcs in report.details.items()
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
        "details": {
            fp: [asdict(fc) for fc in fcs] for fp, fcs in report.details.items()
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"\nJSON report written to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare AST function map against coverage database"
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
        help="Path for JSON report output",
    )
    args = parser.parse_args()

    if not args.src_root.is_dir():
        print(f"ERROR: Source root not found: {args.src_root}", file=sys.stderr)
        return 0

    ast_map = build_ast_map(args.src_root)
    if not args.coverage_db.exists():
        print(f"WARNING: Coverage database not found: {args.coverage_db}", file=sys.stderr)
        print("Run pytest with --cov first to generate coverage data.", file=sys.stderr)
        _print_report(Report(total=sum(len(v) for v in ast_map.values())))
        return 0

    coverage_map = query_coverage_db(args.coverage_db)
    report = compare(ast_map, coverage_map)
    _print_report(report)

    if args.output:
        _write_json(report, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
