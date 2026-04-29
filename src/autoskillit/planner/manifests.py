from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from autoskillit.core import atomic_write, write_versioned_json
from autoskillit.planner.schema import (
    RunDirResult,
    validate_assignment_result,
    validate_phase_result,
    validate_refined_assignments,
    validate_refined_plan,
    validate_wp_result,
)

_NATURAL_SORT_RE = re.compile(r"(\d+)")


class _PhaseBucket(TypedDict):
    id: str
    name: str
    wp_ids: list[str]
    wp_names: list[str]
    wp_scopes: list[str]
    wp_estimated_files: list[list[str]]
    wp_count: int


def _natural_sort_key(s: str) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok for tok in _NATURAL_SORT_RE.split(s)]


def create_run_dir(temp_dir: str) -> RunDirResult:
    if not temp_dir:
        raise ValueError("temp_dir must be a non-empty path")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(temp_dir) / "planner" / f"run-{stamp}-{secrets.token_hex(4)}"
    for sub in ("phases", "assignments", "work_packages"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    return RunDirResult(planner_dir=str(run_dir))


def _build_index_entry(result_data: dict[str, object]) -> dict[str, object]:
    return {
        "id": result_data.get("id", ""),
        "name": result_data.get("name", ""),
        "summary": result_data.get("summary", ""),
    }


def build_phase_assignment_manifest(phases_dir: str, output_dir: str) -> dict[str, str]:
    if not phases_dir or not output_dir:
        raise ValueError("phases_dir and output_dir must not be empty")

    phases_path = Path(phases_dir)
    out_dir = Path(output_dir)
    assign_dir = out_dir.resolve()

    phase_files = sorted(phases_path.glob("*_result.json"))
    parsed_phases = []
    for f in phase_files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        try:
            data = validate_phase_result(raw)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid phase result in {f}: {exc}") from exc
        parsed_phases.append(data)
    parsed_phases.sort(key=lambda d: d["phase_number"])

    items = []
    for phase_data in parsed_phases:
        assignments = phase_data.get("assignments", [])
        items.append(
            {
                "id": phase_data["id"],
                "name": phase_data.get("name", ""),
                "status": "pending",
                "result_path": None,
                "metadata": {
                    "assignment_count": len(assignments),
                    "assignment_ids": [
                        a.get("metadata", {}).get("id", "") or a.get("id", "") for a in assignments
                    ],
                    "assignment_names": [a.get("name", "") for a in assignments],
                },
            }
        )

    manifest = {
        "pass_name": "phase_assignments",
        "result_dir": str(assign_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = assign_dir / "phase_assignment_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


def build_phase_wp_manifest(
    assignments_dir: str, output_dir: str, work_packages_dir: str = ""
) -> dict[str, str]:
    if not assignments_dir or not output_dir:
        raise ValueError("assignments_dir and output_dir must not be empty")

    assign_path = Path(assignments_dir)
    if not assign_path.is_dir():
        raise FileNotFoundError(f"assignments_dir does not exist: {assign_path}")
    out_dir = Path(output_dir)
    wp_dir = (
        Path(work_packages_dir).resolve()
        if work_packages_dir
        else (out_dir / "work_packages").resolve()
    )

    assign_files = list(assign_path.glob("*_result.json"))
    parsed_assignments: list[dict] = []
    for f in assign_files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        try:
            data = validate_assignment_result(raw)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid assignment result in {f}: {exc}") from exc
        parsed_assignments.append(data)
    parsed_assignments.sort(key=lambda d: (d["phase_number"], d["assignment_number"]))

    phase_buckets: dict[int, dict] = {}
    for assign_data in parsed_assignments:
        pn = assign_data["phase_number"]
        an = assign_data["assignment_number"]
        if pn not in phase_buckets:
            phase_buckets[pn] = {
                "phase_name": assign_data.get("phase_name", f"Phase {pn}"),
                "phase_id": f"P{pn}",
                "wp_ids": [],
                "wp_names": [],
                "wp_scopes": [],
                "wp_estimated_files": [],
            }
        for wp_seq, wp in enumerate(assign_data.get("proposed_work_packages", []), start=1):
            wp_id = f"P{pn}-A{an}-WP{wp_seq}"
            phase_buckets[pn]["wp_ids"].append(wp_id)
            phase_buckets[pn]["wp_names"].append(wp.get("name", ""))
            phase_buckets[pn]["wp_scopes"].append(wp.get("scope", ""))
            est_files = wp.get("estimated_files", [])
            if not isinstance(est_files, list):
                est_files = []
            phase_buckets[pn]["wp_estimated_files"].append(est_files)

    items = []
    for pn in sorted(phase_buckets):
        bucket = phase_buckets[pn]
        items.append(
            {
                "id": bucket["phase_id"],
                "name": bucket["phase_name"],
                "status": "pending",
                "result_path": None,
                "metadata": {
                    "wp_count": len(bucket["wp_ids"]),
                    "wp_ids": bucket["wp_ids"],
                    "wp_names": bucket["wp_names"],
                    "wp_scopes": bucket["wp_scopes"],
                    "wp_estimated_files": bucket["wp_estimated_files"],
                },
            }
        )

    sentinel_dir = wp_dir / "wp_sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "pass_name": "phase_work_packages",
        "result_dir": str(sentinel_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "phase_wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(out_dir / "wp_index.json", "[]")
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


def finalize_wp_manifest(work_packages_dir: str, output_dir: str) -> dict[str, str]:
    if not work_packages_dir or not output_dir:
        raise ValueError("work_packages_dir and output_dir must not be empty")

    wp_dir = Path(work_packages_dir)
    if not wp_dir.is_dir():
        raise FileNotFoundError(f"work_packages_dir does not exist: {wp_dir}")
    out_dir = Path(output_dir)

    result_files = sorted(wp_dir.glob("*_result.json"), key=lambda p: _natural_sort_key(p.name))
    items = []
    index_entries = []
    for f in result_files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        try:
            data = validate_wp_result(raw)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid WP result in {f}: {exc}") from exc
        items.append(
            {
                "id": data["id"],
                "name": data["name"],
                "status": "done",
                "result_path": str(f),
                "metadata": {},
            }
        )
        index_entries.append(_build_index_entry(data))

    items.sort(key=lambda i: _natural_sort_key(str(i["id"])))
    index_entries.sort(key=lambda e: _natural_sort_key(str(e["id"])))

    manifest = {
        "pass_name": "work_packages",
        "result_dir": str(wp_dir.resolve()),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(out_dir / "wp_index.json", json.dumps(index_entries, indent=2))
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


def expand_assignments(
    refined_plan_path: str, output_dir: str, **kwargs: object
) -> dict[str, str]:
    plan_file = Path(refined_plan_path)
    try:
        plan = json.loads(plan_file.read_text())
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Failed to parse {plan_file}: {exc.msg}", exc.doc, exc.pos
        ) from exc
    plan = validate_refined_plan(plan)
    phases = plan.get("phases", [])
    task = plan.get("task", "")
    assign_dir = Path(output_dir) / "assignments"
    assign_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, object]] = []
    context_paths: list[str] = []
    item_ids: list[str] = []
    for phase in phases:
        phase_id = phase["id"]
        previews = phase.get("assignments_preview", [])
        if not previews:
            continue
        assignment_ids: list[str] = []
        for idx, a in enumerate(previews, start=1):
            if isinstance(a, dict):
                aid = a.get("id", "")
                if not aid:
                    aid = f"{phase_id}-A{idx}"
                assignment_ids.append(aid)
            else:
                assignment_ids.append(str(a))
        assignment_names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in previews]
        metadata = {
            "assignment_count": len(previews),
            "assignment_ids": assignment_ids,
            "assignment_names": assignment_names,
        }
        items.append(
            {
                "id": phase_id,
                "name": phase.get("name", ""),
                "status": "pending",
                "result_path": None,
                "metadata": metadata,
            }
        )
        context: dict[str, object] = {
            "id": phase_id,
            "name": phase.get("name", ""),
            "task": task,
            "metadata": metadata,
            "prior_results": [],
        }
        ctx_path = assign_dir / f"context_{phase_id}.json"
        write_versioned_json(ctx_path, context, schema_version=1)
        context_paths.append(str(ctx_path))
        item_ids.append(phase_id)

    manifest = {
        "pass_name": "phase_assignments",
        "result_dir": str(assign_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = assign_dir / "phase_assignment_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return {
        "manifest_path": str(manifest_path),
        "context_paths": ",".join(context_paths),
        "item_ids": ",".join(item_ids),
    }


def expand_wps(refined_assignments_path: str, output_dir: str, **kwargs: object) -> dict[str, str]:
    assignments_file = Path(refined_assignments_path)
    try:
        data = json.loads(assignments_file.read_text())
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Failed to parse {assignments_file}: {exc.msg}", exc.doc, exc.pos
        ) from exc
    data = validate_refined_assignments(data)
    assignments = data.get("assignments", [])
    task = data.get("task", "")
    out_dir = Path(output_dir)
    wp_dir = out_dir / "work_packages"
    wp_dir.mkdir(parents=True, exist_ok=True)

    phase_buckets: dict[str, _PhaseBucket] = {}
    for assign in assignments:
        phase_id = assign.get("phase_id", "")
        if not phase_id:
            pn = assign.get("phase_number", 0)
            phase_id = f"P{pn}"
        if phase_id not in phase_buckets:
            phase_buckets[phase_id] = _PhaseBucket(
                id=phase_id,
                name=assign.get("phase_name", f"Phase {phase_id}"),
                wp_ids=[],
                wp_names=[],
                wp_scopes=[],
                wp_estimated_files=[],
                wp_count=0,
            )
        bucket = phase_buckets[phase_id]
        wps = assign.get("proposed_work_packages", [])
        for wp in wps:
            wp_id = wp["id"]
            bucket["wp_ids"].append(wp_id)
            bucket["wp_names"].append(wp.get("name", ""))
            bucket["wp_scopes"].append(wp.get("scope", ""))
            est = wp.get("estimated_files", [])
            if not isinstance(est, list):
                est = []
            bucket["wp_estimated_files"].append(est)
            bucket["wp_count"] += 1

    items: list[dict[str, object]] = []
    context_paths: list[str] = []
    item_ids: list[str] = []
    for phase_id in sorted(phase_buckets):
        bucket = phase_buckets[phase_id]
        metadata = {
            "wp_count": bucket["wp_count"],
            "wp_ids": bucket["wp_ids"],
            "wp_names": bucket["wp_names"],
            "wp_scopes": bucket["wp_scopes"],
            "wp_estimated_files": bucket["wp_estimated_files"],
        }
        items.append(
            {
                "id": phase_id,
                "name": bucket["name"],
                "status": "pending",
                "result_path": None,
                "metadata": metadata,
            }
        )
        context: dict[str, object] = {
            "id": phase_id,
            "name": bucket["name"],
            "task": task,
            "metadata": metadata,
            "prior_results": [],
            "wp_index_path": str(out_dir / "wp_index.json"),
        }
        ctx_path = wp_dir / f"context_{phase_id}.json"
        write_versioned_json(ctx_path, context, schema_version=1)
        context_paths.append(str(ctx_path))
        item_ids.append(str(phase_id))

    manifest = {
        "pass_name": "phase_work_packages",
        "result_dir": str(wp_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "phase_wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(out_dir / "wp_index.json", "[]")
    return {
        "manifest_path": str(manifest_path),
        "context_paths": ",".join(context_paths),
        "item_ids": ",".join(item_ids),
    }
