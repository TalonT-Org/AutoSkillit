#!/usr/bin/env python3
"""Compare pytest-testmon selection against cascade filter selection.

Modes:
    --mode=compare   Set comparison of testmon vs cascade selection (default)
    --mode=overhead  Measure collection-time overhead with/without testmon
    --mode=db-stats  Report .testmondata DB statistics

Exit codes:
    0  Always (evaluation tool, not a gate)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def compute_selection_metrics(
    testmon_selected: set[str],
    cascade_selected: set[str],
) -> dict[str, Any]:
    overlap = testmon_selected & cascade_selected
    union = testmon_selected | cascade_selected
    return {
        "testmon_only": testmon_selected - cascade_selected,
        "cascade_only": cascade_selected - testmon_selected,
        "overlap": overlap,
        "testmon_count": len(testmon_selected),
        "cascade_count": len(cascade_selected),
        "overlap_count": len(overlap),
        "jaccard_similarity": len(overlap) / len(union) if union else 1.0,
    }


def _pytest_cmd() -> list[str]:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-m", "pytest"]
    return ["pytest"]


def _collect_testmon_selection() -> set[str]:
    cmd = [*_pytest_cmd(), "--collect-only", "-q", "--testmon", "-o", "addopts="]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    selected: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and "::" in line and not line.startswith(("=", "-", "no tests")):
            test_file = line.split("::")[0]
            selected.add(test_file)
    return selected


def _load_test_filter_module() -> Any:
    test_filter_path = PROJECT_ROOT / "tests" / "_test_filter.py"
    spec = importlib.util.spec_from_file_location("_test_filter", test_filter_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_cascade_selection(changed_files: list[str]) -> set[str]:
    mod = _load_test_filter_module()
    manifest = mod.load_manifest(PROJECT_ROOT / ".autoskillit" / "test-filter-manifest.yaml")
    scope = mod.build_test_scope(
        changed=[Path(f) for f in changed_files],
        repo_root=PROJECT_ROOT,
        manifest=manifest,
        mode=mod.FilterMode.CONSERVATIVE,
    )
    if scope is None:
        return set()
    return {str(p.relative_to(PROJECT_ROOT / "tests")) for p in scope}


def _run_compare(changed_files: list[str]) -> int:
    if not changed_files:
        print("ERROR: --changed-files required for compare mode")
        return 0

    print(f"Changed files: {changed_files}")
    print()

    testmon_sel = _collect_testmon_selection()
    cascade_sel = _collect_cascade_selection(changed_files)

    metrics = compute_selection_metrics(testmon_sel, cascade_sel)

    print("=== Selection Comparison ===")
    print(f"Testmon selected:  {metrics['testmon_count']} test files")
    print(f"Cascade selected:  {metrics['cascade_count']} test files")
    print(f"Overlap:           {metrics['overlap_count']} test files")
    print(f"Jaccard similarity: {metrics['jaccard_similarity']:.3f}")
    print()

    if metrics["testmon_only"]:
        print("Testmon-only (cascade misses):")
        for f in sorted(metrics["testmon_only"]):
            print(f"  {f}")
        print()

    if metrics["cascade_only"]:
        print("Cascade-only (testmon skips):")
        for f in sorted(metrics["cascade_only"]):
            print(f"  {f}")
        print()

    report = {
        "mode": "compare",
        "changed_files": changed_files,
        "testmon_count": metrics["testmon_count"],
        "cascade_count": metrics["cascade_count"],
        "overlap_count": metrics["overlap_count"],
        "jaccard_similarity": metrics["jaccard_similarity"],
        "testmon_only": sorted(metrics["testmon_only"]),
        "cascade_only": sorted(metrics["cascade_only"]),
        "overlap": sorted(metrics["overlap"]),
    }
    _write_report(report)
    return 0


def _run_overhead() -> int:
    base_cmd = [*_pytest_cmd(), "tests/", "-q", "--co", "-n", "4", "-o", "addopts="]
    testmon_cmd = [*base_cmd, "--testmon"]

    print("Measuring collection overhead...")
    print()

    t0 = time.monotonic()
    subprocess.run(base_cmd, capture_output=True, cwd=str(PROJECT_ROOT))
    base_time = time.monotonic() - t0

    t0 = time.monotonic()
    subprocess.run(testmon_cmd, capture_output=True, cwd=str(PROJECT_ROOT))
    testmon_time = time.monotonic() - t0

    overhead_pct = ((testmon_time - base_time) / base_time * 100) if base_time > 0 else 0

    print(f"Base collection:    {base_time:.2f}s")
    print(f"Testmon collection: {testmon_time:.2f}s")
    print(f"Overhead:           {overhead_pct:+.1f}%")

    report = {
        "mode": "overhead",
        "base_time_s": round(base_time, 3),
        "testmon_time_s": round(testmon_time, 3),
        "overhead_pct": round(overhead_pct, 1),
    }
    _write_report(report)
    return 0


def _run_db_stats() -> int:
    db_path = PROJECT_ROOT / ".testmondata"
    if not db_path.exists():
        print("ERROR: .testmondata not found. Run 'task testmon-build' first.")
        return 0

    size_bytes = db_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM node")
        node_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT file_name) FROM node_file")
        source_file_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT AVG(dep_count) FROM "
            "(SELECT node_id, COUNT(*) as dep_count FROM node_file GROUP BY node_id)"
        )
        avg_deps = cursor.fetchone()[0] or 0
    finally:
        conn.close()

    print("=== .testmondata Statistics ===")
    print(f"DB size:            {size_mb:.1f} MB ({size_bytes:,} bytes)")
    print(f"Test nodes:         {node_count:,}")
    print(f"Source files:       {source_file_count:,}")
    print(f"Avg deps per test:  {avg_deps:.1f}")

    report = {
        "mode": "db-stats",
        "db_size_bytes": size_bytes,
        "db_size_mb": round(size_mb, 1),
        "test_node_count": node_count,
        "source_file_count": source_file_count,
        "avg_deps_per_test": round(avg_deps, 1),
    }
    _write_report(report)
    return 0


def _write_report(data: dict[str, Any]) -> None:
    report_dir = PROJECT_ROOT / ".autoskillit" / "temp"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"testmon-eval-{timestamp}.json"
    report_path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    print(f"\nReport written: {report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["compare", "overhead", "db-stats"],
        default="compare",
    )
    parser.add_argument(
        "--changed-files",
        help="Comma-separated list of changed file paths",
    )
    args = parser.parse_args()

    changed_files: list[str] = []
    if args.changed_files:
        changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]

    if args.mode == "compare":
        return _run_compare(changed_files)
    elif args.mode == "overhead":
        return _run_overhead()
    elif args.mode == "db-stats":
        return _run_db_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
