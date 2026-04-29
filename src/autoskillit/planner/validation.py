"""Planner validation: structural completeness, DAG acyclicity, sizing bounds.

All loaders read from individual ``*_result.json`` files in the ``phases/``,
``assignments/``, and ``work_packages/`` subdirectories.  Combined documents
(``combined_*.json``, ``refined_*.json``) are intermediate orchestration
artifacts produced by the merge/refine cycle — they are **not** authoritative
and are never consumed here.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from autoskillit.core import get_logger, write_versioned_json
from autoskillit.planner.schema import (
    ValidationFinding,
    validate_assignment_result,
    validate_phase_result,
    validate_wp_result,
)

logger = get_logger(__name__)


def _load_phase_results(root: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for f in sorted((root / "phases").glob("*_result.json")):
        try:
            raw = json.loads(f.read_text())
            data = validate_phase_result(raw)
            phase_id = f"P{data['phase_number']}"
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed phase result file {f}: {exc}") from exc
        results[phase_id] = data
    return results


def _load_assignment_results(root: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    assign_dir = root / "assignments"
    if not assign_dir.exists():
        return results
    for f in sorted(assign_dir.glob("*_result.json")):
        try:
            raw = json.loads(f.read_text())
            data = validate_assignment_result(raw)
            assign_id = f"P{data['phase_number']}-A{data['assignment_number']}"
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed assignment result file {f}: {exc}") from exc
        results[assign_id] = data
    return results


def _load_wp_results(root: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    wp_dir = root / "work_packages"
    if not wp_dir.exists():
        return results
    for f in sorted(wp_dir.glob("*_result.json")):
        if f.name in ("wp_manifest.json", "wp_index.json"):
            continue
        try:
            raw = json.loads(f.read_text())
            data = validate_wp_result(raw)
            results[data["id"]] = data
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed WP result file {f}: {exc}") from exc
    return results


def _load_wp_manifest(root: Path) -> dict | None:
    manifest_path = root / "work_packages" / "wp_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Malformed WP manifest file {manifest_path}: {exc}") from exc


def _inject_backward_deps(wp_results: dict[str, dict], dep_graph: dict) -> None:
    for wp_id, extra_deps in dep_graph.get("added_backward_deps", {}).items():
        if wp_id not in wp_results:
            continue
        existing = wp_results[wp_id].setdefault("depends_on", [])
        for dep in extra_deps:
            if dep not in existing:
                existing.append(dep)


def _check_phase_completeness(
    phase_results: dict[str, dict],
    assignment_results: dict[str, dict],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    assigned_phase_nums = {v["phase_number"] for v in assignment_results.values()}
    for phase_id, phase in phase_results.items():
        if phase["phase_number"] not in assigned_phase_nums:
            findings.append(
                {
                    "message": f"Phase {phase_id} has no assignments",
                    "severity": "error",
                    "check": "phase_completeness",
                }
            )
    return findings


def _check_assignment_completeness(
    assignment_results: dict[str, dict],
    wp_results: dict[str, dict],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    wp_pairs: set[tuple[int, int]] = set()
    for wp_id in wp_results:
        parts = wp_id.split("-")
        if len(parts) < 2:
            findings.append(
                {
                    "message": f"WP {wp_id!r} has malformed id (expected PX-AY-WPZ)",
                    "severity": "error",
                    "check": "assignment_completeness",
                }
            )
            continue
        phase_num = int(parts[0][1:])
        assign_num = int(parts[1][1:])
        wp_pairs.add((phase_num, assign_num))
    for assign_id, assign in assignment_results.items():
        pair = (assign["phase_number"], assign["assignment_number"])
        if pair not in wp_pairs:
            findings.append(
                {
                    "message": f"Assignment {assign_id} has no work packages",
                    "severity": "error",
                    "check": "assignment_completeness",
                }
            )
    return findings


def _check_dep_references(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep not in wp_results:
                findings.append(
                    {
                        "message": f"WP {wp_id} depends on unknown WP {dep}",
                        "severity": "error",
                        "check": "dep_references",
                    }
                )
    return findings


def _check_dag_acyclic(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    in_degree: dict[str, int] = {wp_id: 0 for wp_id in wp_results}
    adjacency: dict[str, list[str]] = {wp_id: [] for wp_id in wp_results}
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep in wp_results:
                adjacency[dep].append(wp_id)
                in_degree[wp_id] += 1

    queue: deque[str] = deque(k for k, v in in_degree.items() if v == 0)
    sorted_nodes: list[str] = []
    while queue:
        node = queue.popleft()
        sorted_nodes.append(node)
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_nodes) < len(wp_results):
        cycle_nodes = [n for n in wp_results if n not in set(sorted_nodes)]
        return [
            {
                "message": f"Cycle detected among WPs: {', '.join(sorted(cycle_nodes))}",
                "severity": "error",
                "check": "dag_acyclic",
            }
        ]
    return []


def _check_sizing_bounds(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for wp_id, wp in wp_results.items():
        count = len(wp.get("deliverables", []))
        if not (1 <= count <= 5):
            findings.append(
                {
                    "message": f"WP {wp_id} has {count} deliverables (must be 1–5)",
                    "severity": "error",
                    "check": "sizing_bounds",
                }
            )
    return findings


def _check_duplicate_deliverables(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    deliverable_map: dict[str, list[str]] = {}
    for wp_id, wp in wp_results.items():
        for d in wp.get("deliverables", []):
            deliverable_map.setdefault(d, []).append(wp_id)
    findings: list[ValidationFinding] = []
    for path, owners in deliverable_map.items():
        if len(owners) > 1:
            findings.append(
                {
                    "message": (
                        f"Deliverable '{path}' claimed by multiple WPs: "
                        f"{', '.join(sorted(owners))}"
                    ),
                    "severity": "error",
                    "check": "duplicate_deliverables",
                }
            )
    return findings


def _check_duplicate_files_touched(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    file_map: dict[str, list[str]] = {}
    for wp_id, wp in wp_results.items():
        for path in wp.get("files_touched", []):
            file_map.setdefault(path, []).append(wp_id)
    findings: list[ValidationFinding] = []
    for path, owners in file_map.items():
        if len(owners) > 1:
            findings.append(
                {
                    "message": (
                        f"File '{path}' touched by multiple WPs: {', '.join(sorted(owners))}"
                    ),
                    "severity": "warning",
                    "check": "duplicate_files_touched",
                }
            )
    return findings


def _check_failed_wps(wp_manifest: dict | None) -> list[ValidationFinding]:
    if wp_manifest is None:
        return []
    findings: list[ValidationFinding] = []
    for item in wp_manifest.get("items", []):
        if item.get("status") == "failed":
            findings.append(
                {
                    "message": f"WP {item.get('id', '<unknown>')} has status 'failed'",
                    "severity": "error",
                    "check": "failed_wps",
                }
            )
    return findings


def validate_plan(output_dir: str) -> dict[str, str]:
    root = Path(output_dir)
    phase_results = _load_phase_results(root)
    assignment_results = _load_assignment_results(root)
    wp_results = _load_wp_results(root)
    wp_manifest = _load_wp_manifest(root)

    dep_graph_path = root / "dep_graph.json"
    if dep_graph_path.exists():
        try:
            dep_graph = json.loads(dep_graph_path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed dep graph file {dep_graph_path}: {exc}") from exc
        _inject_backward_deps(wp_results, dep_graph)

    all_findings: list[ValidationFinding] = []
    all_findings.extend(_check_phase_completeness(phase_results, assignment_results))
    all_findings.extend(_check_assignment_completeness(assignment_results, wp_results))
    all_findings.extend(_check_dep_references(wp_results))
    all_findings.extend(_check_dag_acyclic(wp_results))
    all_findings.extend(_check_sizing_bounds(wp_results))
    all_findings.extend(_check_duplicate_deliverables(wp_results))
    all_findings.extend(_check_duplicate_files_touched(wp_results))
    all_findings.extend(_check_failed_wps(wp_manifest))

    errors = [f for f in all_findings if f["severity"] == "error"]
    warnings = [f for f in all_findings if f["severity"] == "warning"]
    unrecognized = [f for f in all_findings if f["severity"] not in ("error", "warning")]
    if unrecognized:
        sev_vals = {f["severity"] for f in unrecognized}
        raise ValueError(f"Unrecognized severity values in findings: {sev_vals}")

    verdict = "pass" if not errors else "fail"
    validation_path = root / "validation.json"
    write_versioned_json(
        validation_path,
        {"verdict": verdict, "findings": errors, "warnings": warnings},
        schema_version=2,
    )
    logger.info("validate_plan", verdict=verdict, issue_count=len(errors))
    return {
        "verdict": verdict,
        "validation_path": str(validation_path),
        "issue_count": str(len(errors)),
        "warning_count": str(len(warnings)),
    }
