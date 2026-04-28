from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, write_versioned_json
from autoskillit.planner.validation import (
    _load_assignment_results,
    _load_phase_results,
    _load_wp_results,
)

logger = get_logger(__name__)


def _topological_sort(wp_results: dict[str, dict]) -> list[str]:
    in_degree: dict[str, int] = {wp_id: 0 for wp_id in wp_results}
    adjacency: dict[str, list[str]] = {wp_id: [] for wp_id in wp_results}
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep in wp_results:
                adjacency[dep].append(wp_id)
                in_degree[wp_id] += 1

    queue: deque[str] = deque(sorted(k for k, v in in_degree.items() if v == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(adjacency[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) < len(wp_results):
        cycle_nodes = [n for n in wp_results if n not in set(order)]
        raise RuntimeError(f"Cycle detected among WPs: {', '.join(sorted(cycle_nodes))}")
    return order


def _inject_forward_deps(wp_results: dict[str, dict], dep_graph: dict) -> None:
    for wp_id, dependents in dep_graph.get("forward_deps", {}).items():
        if wp_id in wp_results:
            wp_results[wp_id]["depended_on_by"] = list(dependents)


def _build_phase_lookup(phase_results: dict[str, dict]) -> dict[int, dict]:
    return {v["phase_number"]: v for v in phase_results.values()}


def _build_assignment_lookup(
    assignment_results: dict[str, dict],
) -> dict[tuple[int, int], dict]:
    return {(v["phase_number"], v["assignment_number"]): v for v in assignment_results.values()}


def _parse_wp_id(wp_id: str) -> tuple[int, int, int]:
    parts = wp_id.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid WP id format (expected PX-AY-WPZ): {wp_id!r}")
    try:
        return int(parts[0][1:]), int(parts[1][1:]), int(parts[2][2:])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Invalid WP id format (expected PX-AY-WPZ): {wp_id!r}") from exc


def _render_issue_body(wp: dict, phase: dict, assignment: dict) -> str:
    phase_num = phase["phase_number"]
    phase_name = phase["name"]
    phase_slug = phase["name_slug"]
    milestone = f"{phase_num}-{phase_slug}"
    assign_id = f"P{phase['phase_number']}-A{assignment['assignment_number']}"
    assign_name = assignment.get("name", "")

    depends_on = wp.get("depends_on", [])
    depended_on_by = wp.get("depended_on_by", [])

    depends_str = ", ".join(depends_on) if depends_on else "None"
    depended_str = ", ".join(depended_on_by) if depended_on_by else "None"

    deliverables_md = "\n".join(f"- `{d}`" for d in wp.get("deliverables", []))
    steps_md = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(wp.get("technical_steps", [])))
    criteria_md = "\n".join(f"- {c}" for c in wp.get("acceptance_criteria", []))

    return f"""## Goal

{wp.get("goal", wp.get("summary", ""))}

## Context

- Phase: {phase_name} (Milestone: {milestone})
- Assignment: {assign_id} ({assign_name})
- Depends on: {depends_str}
- Depended on by: {depended_str}

## Deliverables

{deliverables_md}

## Technical Steps

{steps_md}

## Acceptance Criteria

{criteria_md}
"""


def compile_plan(output_dir: str, task: str, source_dir: str) -> dict[str, str]:
    root = Path(output_dir)

    phase_results = _load_phase_results(root)
    assignment_results = _load_assignment_results(root)
    wp_results = _load_wp_results(root)

    validation_path = root / "validation.json"
    if validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed validation file {validation_path}: {exc}") from exc
        if validation.get("verdict") != "pass":
            logger.warning(
                "compile_plan called with non-passing validation",
                verdict=validation.get("verdict"),
            )

    dep_graph: dict = {}
    dep_graph_path = root / "dep_graph.json"
    if dep_graph_path.exists():
        try:
            dep_graph = json.loads(dep_graph_path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed dep graph file {dep_graph_path}: {exc}") from exc

    _inject_forward_deps(wp_results, dep_graph)

    execution_order = _topological_sort(wp_results)
    phase_lookup = _build_phase_lookup(phase_results)
    assign_lookup = _build_assignment_lookup(assignment_results)

    issues_dir = root / "issues"
    issues_dir.mkdir(exist_ok=True)

    issue_paths: dict[str, str] = {}
    for wp_id in execution_order:
        wp = wp_results[wp_id]
        phase_num, assign_num, _ = _parse_wp_id(wp_id)
        if phase_num not in phase_lookup:
            raise RuntimeError(
                f"WP {wp_id!r} references phase {phase_num} not in loaded phase results"
            )
        phase = phase_lookup[phase_num]
        if (phase_num, assign_num) not in assign_lookup:
            raise RuntimeError(
                f"WP {wp_id!r} references assignment P{phase_num}-A{assign_num}"
                " not in loaded assignment results"
            )
        assignment = assign_lookup[(phase_num, assign_num)]
        body = _render_issue_body(wp, phase, assignment)
        issue_path = issues_dir / f"{wp_id}_issue.md"
        atomic_write(issue_path, body)
        issue_paths[wp_id] = str(issue_path)

    milestones = [
        {
            "phase_number": phase["phase_number"],
            "name": phase["name"],
            "name_slug": phase["name_slug"],
        }
        for phase in sorted(phase_results.values(), key=lambda p: p["phase_number"])
    ]
    milestones_path = root / "milestones.json"
    write_versioned_json(milestones_path, {"milestones": milestones}, schema_version=1)

    phases_nested = []
    for phase_id in sorted(phase_results, key=lambda k: phase_results[k]["phase_number"]):
        phase = phase_results[phase_id]
        phase_num = phase["phase_number"]
        assignments_nested = []
        for (pn, an), assign in sorted(assign_lookup.items()):
            if pn != phase_num:
                continue
            wps_in_assign = [
                wp_results[wid] for wid in execution_order if wid.startswith(f"P{pn}-A{an}-")
            ]
            assignments_nested.append({**assign, "work_packages": wps_in_assign})
        phases_nested.append({**phase, "assignments": assignments_nested})

    plan_json_path = root / "plan.json"
    write_versioned_json(
        plan_json_path,
        {
            "task": task,
            "source_dir": source_dir,
            "phases": phases_nested,
            "execution_order": execution_order,
        },
        schema_version=1,
    )

    md_lines = [f"# Plan: {task}", ""]
    for phase in sorted(phase_results.values(), key=lambda p: p["phase_number"]):
        phase_num = phase["phase_number"]
        md_lines.append(f"## Phase {phase_num}: {phase['name']}")
        md_lines.append("")
        for wp_id in execution_order:
            pn, an, _ = _parse_wp_id(wp_id)
            if pn != phase_num:
                continue
            wp = wp_results[wp_id]
            md_lines.append(f"### {wp_id}: {wp.get('name', '')}")
            md_lines.append("")
            md_lines.append(wp.get("summary", wp.get("goal", "")))
            md_lines.append("")

    plan_md_path = root / "plan.md"
    atomic_write(plan_md_path, "\n".join(md_lines))

    manifest_path = root / "manifest.json"
    write_versioned_json(
        manifest_path,
        {
            "task": task,
            "source_dir": source_dir,
            "execution_order": execution_order,
            "issues": issue_paths,
        },
        schema_version=1,
    )

    plan_parts = "\n".join(issue_paths[wp_id] for wp_id in execution_order)

    logger.info("compile_plan", wp_count=len(execution_order))
    return {
        "plan_path": str(plan_md_path),
        "plan_json_path": str(plan_json_path),
        "plan_parts": plan_parts,
    }
