"""Planner validation: structural completeness, DAG acyclicity, sizing bounds.

All loaders read from individual ``*_result.json`` files in the ``phases/``,
``assignments/``, and ``work_packages/`` subdirectories.  Combined documents
(``combined_*.json``, ``refined_*.json``) are intermediate orchestration
artifacts produced by the merge/refine cycle — they are **not** authoritative
and are never consumed here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

import regex as re

from autoskillit.core import get_logger, read_versioned_json, write_versioned_json
from autoskillit.planner._dag_ops import find_sccs, topological_sort
from autoskillit.planner.schema import (
    ASSIGN_RESULT_FILE_RE,
    DELIVERABLE_BOUNDS,
    PHASE_RESULT_FILE_RE,
    WP_RESULT_FILE_RE,
    ValidationFinding,
    validate_assignment_result,
    validate_phase_result,
    validate_wp_result,
)

logger = get_logger(__name__)

_VERSION_BUMP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(?:bump|edit|set|update|modify|change)\s[\s\S]{0,40}pyproject\.toml",
        r"pyproject\.toml[\s\S]{0,40}version\s*=",
        r"sync[-_]versions",
        r"task\s+sync-versions",
        r"\bversion[\s_-]*bump\b",
    ]
)

_NEGATION_PREFIX_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:no|not|skip|avoid|omit|don'?t|do\s+not)\b",
    re.IGNORECASE,
)


class DiscoveryResult(NamedTuple):
    accepted: list[Path]
    rejected: list[Path]


def discover_tier_files(directory: Path, filename_re: re.Pattern[str]) -> DiscoveryResult:
    """Partition *_result.json files into those matching the tier regex and those not."""
    accepted: list[Path] = []
    rejected: list[Path] = []
    for f in sorted(directory.glob("*_result.json")):
        if filename_re.match(f.name):
            accepted.append(f)
        else:
            rejected.append(f)
    return DiscoveryResult(accepted=accepted, rejected=rejected)


def _load_phase_results(root: Path) -> tuple[dict[str, dict], list[Path]]:
    results: dict[str, dict] = {}
    phases_dir = root / "phases"
    if not phases_dir.exists():
        return results, []
    discovery = discover_tier_files(phases_dir, PHASE_RESULT_FILE_RE)
    for f in discovery.accepted:
        try:
            raw = json.loads(f.read_text())
            data = validate_phase_result(raw)
            phase_id = f"P{data['phase_number']}"
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed phase result file {f}: {exc}") from exc
        results[phase_id] = data
    return results, discovery.rejected


def _load_assignment_results(root: Path) -> tuple[dict[str, dict], list[Path]]:
    results: dict[str, dict] = {}
    assign_dir = root / "assignments"
    if not assign_dir.exists():
        return results, []
    discovery = discover_tier_files(assign_dir, ASSIGN_RESULT_FILE_RE)
    for f in discovery.accepted:
        try:
            raw = json.loads(f.read_text())
            data = validate_assignment_result(raw)
            assign_id = f"P{data['phase_number']}-A{data['assignment_number']}"
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed assignment result file {f}: {exc}") from exc
        results[assign_id] = data
    return results, discovery.rejected


def _load_wp_results(root: Path) -> tuple[dict[str, dict], list[Path]]:
    results: dict[str, dict] = {}
    wp_dir = root / "work_packages"
    if not wp_dir.exists():
        return results, []
    discovery = discover_tier_files(wp_dir, WP_RESULT_FILE_RE)
    for f in discovery.accepted:
        try:
            raw = json.loads(f.read_text())
            data = validate_wp_result(raw, allow_stub=True)
            results[data["id"]] = data
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(f"Malformed WP result file {f}: {exc}") from exc
    return results, discovery.rejected


def _load_wp_manifest(root: Path) -> dict | None:
    manifest_path = root / "work_packages" / "wp_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Malformed WP manifest file {manifest_path}: {exc}") from exc


def _load_absorption_registry(root: Path) -> dict[str, Any] | None:
    registry_path = root / "work_packages" / "absorption_registry.json"
    if not registry_path.exists():
        return None
    return read_versioned_json(registry_path, 1)


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
    absorption_registry: dict[str, Any] | None = None,
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
    absorbed_pairs: set[tuple[int, int]] = set()
    if absorption_registry and isinstance(absorption_registry.get("absorbed"), dict):
        for absorbed_id in absorption_registry["absorbed"]:
            parts = absorbed_id.split("-")
            if len(parts) >= 2:
                try:
                    absorbed_pairs.add((int(parts[0][1:]), int(parts[1][1:])))
                except ValueError:
                    logger.warning("malformed_absorbed_id", absorbed_id=absorbed_id)
    for assign_id, assign in assignment_results.items():
        pair = (assign["phase_number"], assign["assignment_number"])
        if pair in absorbed_pairs:
            continue
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
            if dep == wp_id:
                findings.append(
                    {
                        "message": f"WP {wp_id} depends on itself",
                        "severity": "error",
                        "check": "dep_references",
                    }
                )
    return findings


def _check_dag_acyclic(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    adjacency: dict[str, list[str]] = {wp_id: [] for wp_id in wp_results}
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep in wp_results:
                adjacency[dep].append(wp_id)

    try:
        topological_sort(wp_results)
    except RuntimeError as exc:
        logger.warning("topological_sort raised during DAG check: %s", exc)
        sccs = find_sccs(adjacency)
        if not sccs:
            return [
                {
                    "message": f"Cycle detected among WPs (SCC analysis inconclusive): {exc}",
                    "severity": "error",
                    "check": "dag_acyclic",
                }
            ]
        cycle_nodes = sorted(set(node for scc in sccs for node in scc))

        if len(cycle_nodes) == 2:
            a, b = cycle_nodes
            a_deps = set(wp_results.get(a, {}).get("depends_on", []))
            b_deps = set(wp_results.get(b, {}).get("depends_on", []))
            if b in a_deps and a in b_deps:
                return [
                    {
                        "message": f"Cycle detected among WPs: {a}, {b}",
                        "severity": "error",
                        "check": "dag_acyclic",
                        "cycle_size": 2,
                        "cycle_nodes": [a, b],
                        "cycle_edges": [[a, b], [b, a]],
                    }
                ]
        return [
            {
                "message": f"Cycle detected among WPs: {', '.join(sorted(cycle_nodes))}",
                "severity": "error",
                "check": "dag_acyclic",
                "cycle_size": len(cycle_nodes),
                "cycle_nodes": cycle_nodes,
            }
        ]
    return []


def _check_sizing_bounds(wp_results: dict[str, dict]) -> list[ValidationFinding]:
    lo, hi = DELIVERABLE_BOUNDS
    findings: list[ValidationFinding] = []
    for wp_id, wp in wp_results.items():
        count = len(wp.get("deliverables", []))
        if count < lo:
            findings.append(
                {
                    "message": f"WP {wp_id} has {count} deliverables (below {lo})",
                    "severity": "error",
                    "check": "sizing_bounds",
                }
            )
        elif count > hi:
            findings.append(
                {
                    "message": f"WP {wp_id} has {count} deliverables (exceeds {hi})",
                    "severity": "warning",
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


def _check_version_bump_steps(
    wp_results: dict[str, dict],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for wp_id, wp in wp_results.items():
        steps: list[str] = wp.get("technical_steps") or []
        fragments = [wp.get("name") or "", wp.get("summary") or ""] + steps
        if any(
            fragment
            and not _NEGATION_PREFIX_RE.search(fragment)
            and any(pattern.search(fragment) for pattern in _VERSION_BUMP_PATTERNS)
            for fragment in fragments
        ):
            findings.append(
                {
                    "message": (
                        f"{wp_id}: contains manual version-bump steps; "
                        "CI handles version bumps automatically on merge — "
                        "remove these steps."
                    ),
                    "severity": "warning",
                    "check": "version_bump_step",
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
    phase_results, phase_rejected = _load_phase_results(root)
    assignment_results, assign_rejected = _load_assignment_results(root)
    wp_results, wp_rejected = _load_wp_results(root)
    wp_manifest = _load_wp_manifest(root)
    absorption_registry = _load_absorption_registry(root)

    dep_graph_path = root / "dep_graph.json"
    if dep_graph_path.exists():
        try:
            dep_graph = json.loads(dep_graph_path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed dep graph file {dep_graph_path}: {exc}") from exc
        _inject_backward_deps(wp_results, dep_graph)

    all_findings: list[ValidationFinding] = []
    all_findings.extend(_check_phase_completeness(phase_results, assignment_results))
    all_findings.extend(
        _check_assignment_completeness(assignment_results, wp_results, absorption_registry)
    )
    all_findings.extend(_check_dep_references(wp_results))
    all_findings.extend(_check_dag_acyclic(wp_results))
    all_findings.extend(_check_sizing_bounds(wp_results))
    all_findings.extend(_check_duplicate_deliverables(wp_results))
    all_findings.extend(_check_duplicate_files_touched(wp_results))
    all_findings.extend(_check_version_bump_steps(wp_results))
    all_findings.extend(_check_failed_wps(wp_manifest))

    discovery_warnings: list[ValidationFinding] = []
    for f in phase_rejected:
        discovery_warnings.append(
            {
                "message": f"phase file {f.name} does not match phase naming pattern",
                "severity": "warning",
                "check": "file_discovery_miss",
            }
        )
    for f in assign_rejected:
        discovery_warnings.append(
            {
                "message": f"assignment file {f.name} does not match assignment naming pattern",
                "severity": "warning",
                "check": "file_discovery_miss",
            }
        )
    for f in wp_rejected:
        discovery_warnings.append(
            {
                "message": f"work package file {f.name} does not match WP naming pattern",
                "severity": "warning",
                "check": "file_discovery_miss",
            }
        )
    all_findings.extend(discovery_warnings)

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
