from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from autoskillit.core import get_logger, write_versioned_json
from autoskillit.planner.schema import (
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
) -> list[str]:
    findings: list[str] = []
    assigned_phase_nums = {v["phase_number"] for v in assignment_results.values()}
    for phase_id, phase in phase_results.items():
        if phase["phase_number"] not in assigned_phase_nums:
            findings.append(f"Phase {phase_id} has no assignments")
    return findings


def _check_assignment_completeness(
    assignment_results: dict[str, dict],
    wp_results: dict[str, dict],
) -> list[str]:
    findings: list[str] = []
    wp_pairs: set[tuple[int, int]] = set()
    for wp_id in wp_results:
        parts = wp_id.split("-")
        if len(parts) < 2:
            findings.append(f"WP {wp_id!r} has malformed id (expected PX-AY-WPZ)")
            continue
        phase_num = int(parts[0][1:])
        assign_num = int(parts[1][1:])
        wp_pairs.add((phase_num, assign_num))
    for assign_id, assign in assignment_results.items():
        pair = (assign["phase_number"], assign["assignment_number"])
        if pair not in wp_pairs:
            findings.append(f"Assignment {assign_id} has no work packages")
    return findings


def _check_dep_references(wp_results: dict[str, dict]) -> list[str]:
    findings: list[str] = []
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep not in wp_results:
                findings.append(f"WP {wp_id} depends on unknown WP {dep}")
    return findings


def _check_dag_acyclic(wp_results: dict[str, dict]) -> list[str]:
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
        return [f"Cycle detected among WPs: {', '.join(sorted(cycle_nodes))}"]
    return []


def _check_sizing_bounds(wp_results: dict[str, dict]) -> list[str]:
    findings: list[str] = []
    for wp_id, wp in wp_results.items():
        count = len(wp.get("deliverables", []))
        if not (1 <= count <= 5):
            findings.append(f"WP {wp_id} has {count} deliverables (must be 1–5)")
    return findings


def _check_duplicate_deliverables(wp_results: dict[str, dict]) -> list[str]:
    deliverable_map: dict[str, list[str]] = {}
    for wp_id, wp in wp_results.items():
        for d in wp.get("deliverables", []):
            deliverable_map.setdefault(d, []).append(wp_id)
    findings: list[str] = []
    for path, owners in deliverable_map.items():
        if len(owners) > 1:
            findings.append(
                f"Deliverable '{path}' claimed by multiple WPs: {', '.join(sorted(owners))}"
            )
    return findings


def _check_duplicate_files_touched(wp_results: dict[str, dict]) -> list[str]:
    file_map: dict[str, list[str]] = {}
    for wp_id, wp in wp_results.items():
        for path in wp.get("files_touched", []):
            file_map.setdefault(path, []).append(wp_id)
    findings: list[str] = []
    for path, owners in file_map.items():
        if len(owners) > 1:
            findings.append(f"File '{path}' touched by multiple WPs: {', '.join(sorted(owners))}")
    return findings


def _check_failed_wps(wp_manifest: dict | None) -> list[str]:
    if wp_manifest is None:
        return []
    findings: list[str] = []
    for item in wp_manifest.get("items", []):
        if item.get("status") == "failed":
            findings.append(f"WP {item.get('id', '<unknown>')} has status 'failed'")
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

    findings: list[str] = []
    findings.extend(_check_phase_completeness(phase_results, assignment_results))
    findings.extend(_check_assignment_completeness(assignment_results, wp_results))
    findings.extend(_check_dep_references(wp_results))
    findings.extend(_check_dag_acyclic(wp_results))
    findings.extend(_check_sizing_bounds(wp_results))
    findings.extend(_check_duplicate_deliverables(wp_results))
    findings.extend(_check_duplicate_files_touched(wp_results))
    findings.extend(_check_failed_wps(wp_manifest))

    verdict = "pass" if not findings else "fail"
    validation_path = root / "validation.json"
    write_versioned_json(
        validation_path,
        {"verdict": verdict, "findings": findings},
        schema_version=1,
    )
    logger.info("validate_plan", verdict=verdict, issue_count=len(findings))
    return {
        "verdict": verdict,
        "validation_path": str(validation_path),
        "issue_count": str(len(findings)),
    }
