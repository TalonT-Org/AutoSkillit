from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, write_versioned_json
from autoskillit.planner.schema import (
    RunDirResult,
    validate_assignment_result,
    validate_phase_result,
    validate_wp_result,
)

_logger = get_logger(__name__)

_NATURAL_SORT_RE = re.compile(r"(\d+)")


def _natural_sort_key(s: str) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok for tok in _NATURAL_SORT_RE.split(s)]


def create_run_dir() -> RunDirResult:
    temp = os.environ.get("AUTOSKILLIT_TEMP")
    if not temp:
        raise RuntimeError("AUTOSKILLIT_TEMP must be set before calling create_run_dir()")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(temp) / "planner" / f"run-{stamp}-{secrets.token_hex(4)}"
    for sub in ("phases", "assignments", "work_packages"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    return RunDirResult(planner_dir=str(run_dir))


def build_pre_elab_snapshot(manifest_path: str, output_dir: str) -> dict[str, str]:
    manifest_file = Path(manifest_path)
    out_dir = Path(output_dir)
    try:
        manifest = json.loads(manifest_file.read_text())
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Failed to parse {manifest_file}: {exc.msg}", exc.doc, exc.pos
        ) from exc
    items = manifest.get("items", [])
    phases = []
    for item in items:
        metadata = item.get("metadata", {})
        phases.append(
            {
                "id": item["id"],
                "name": item.get("name", ""),
                "goal": metadata.get("goal", ""),
                "scope": metadata.get("scope", []),
                "ordering": metadata.get("ordering", 0),
            }
        )
    phases.sort(key=lambda p: p["ordering"])
    snapshot_path = out_dir / "plan_snapshot.json"
    write_versioned_json(
        snapshot_path,
        {"task": "", "source_dir": "", "phases": phases},
        schema_version=1,
    )
    return {"snapshot_path": str(snapshot_path)}


def _build_index_entry(result_data: dict[str, object]) -> dict[str, object]:
    return {
        "id": result_data.get("id", ""),
        "name": result_data.get("name", ""),
        "summary": result_data.get("summary", ""),
    }


def _backstop_wp_index(item_id: str, result_path: Path, output_dir: Path) -> None:
    wp_index_path = output_dir / "wp_index.json"
    if not wp_index_path.exists():
        return
    try:
        index = json.loads(wp_index_path.read_text())
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Failed to parse {wp_index_path}: {exc.msg}", exc.doc, exc.pos
        ) from exc
    if not isinstance(index, list):
        raise TypeError(f"Expected list in {wp_index_path}, got {type(index).__name__}")
    indexed_ids = {entry["id"] for entry in index}
    if item_id not in indexed_ids:
        result_data = json.loads(result_path.read_text())
        index.append(_build_index_entry(result_data))
        atomic_write(wp_index_path, json.dumps(index, indent=2))


def check_remaining(manifest_path: str, pass_name: str, output_dir: str) -> dict[str, str]:
    manifest_file = Path(manifest_path)
    out_dir = Path(output_dir)
    try:
        manifest = json.loads(manifest_file.read_text())
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Failed to parse {manifest_file}: {exc.msg}", exc.doc, exc.pos
        ) from exc
    items = manifest.get("items")
    if not isinstance(items, list):
        raise ValueError(
            f"Manifest {manifest_file} missing or invalid 'items' field"
            f" (got {type(items).__name__})"
        )

    result_dir_str = manifest.get("result_dir")
    if result_dir_str is None:
        raise ValueError(
            f"Manifest at {manifest_file} is missing required 'result_dir' field. "
            f"Re-run build_assignment_manifest or build_wp_manifest to regenerate."
        )
    result_dir = Path(result_dir_str)

    for item in items:
        if item["status"] == "processing":
            result_path = result_dir / f"{item['id']}_result.json"
            if not result_path.exists():
                for _attempt in range(2):
                    time.sleep(1)
                    if result_path.exists():
                        break
                else:
                    _logger.warning(
                        "check_remaining: no result file after retries — marking failed",
                        item_id=item["id"],
                        result_path=str(result_path),
                    )
                    item["status"] = "failed"
                    continue
            item["status"] = "done"
            item["result_path"] = str(result_path)
            if pass_name == "work_packages":
                _backstop_wp_index(item["id"], result_path, out_dir)

    next_item = next((item for item in items if item["status"] == "pending"), None)

    if next_item is not None:
        next_item["status"] = "processing"
        prior_results = [
            str(i.get("result_path"))
            for i in items
            if i["status"] == "done" and i.get("result_path")
        ]
        context = {
            "id": next_item["id"],
            "name": next_item["name"],
            "metadata": next_item["metadata"],
            "prior_results": prior_results,
            "wp_index_path": str(out_dir / "wp_index.json"),
        }
        context_path = out_dir / f"context_{next_item['id']}.json"
        write_versioned_json(context_path, context, schema_version=1)
        write_versioned_json(manifest_file, manifest, schema_version=1)
        return {
            "current_item_path": str(context_path),
            "current_item_id": str(next_item["id"]),
            "has_remaining": "true",
        }

    write_versioned_json(manifest_file, manifest, schema_version=1)
    return {"current_item_path": "", "current_item_id": "", "has_remaining": "false"}


def build_assignment_manifest(
    phases_dir: str, assignments_dir: str, output_dir: str
) -> dict[str, str]:
    if not phases_dir or not assignments_dir or not output_dir:
        raise ValueError("phases_dir, assignments_dir, and output_dir must not be empty")

    phases_path = Path(phases_dir)
    out_dir = Path(output_dir)
    assign_dir = Path(assignments_dir).resolve()

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
        phase_number = phase_data["phase_number"]
        assignments = phase_data["assignments"]
        for seq, assignment in enumerate(assignments, start=1):
            item_id = f"P{phase_number}-A{seq}"
            items.append(
                {
                    "id": item_id,
                    "name": assignment.get("name", ""),
                    "status": "pending",
                    "result_path": None,
                    "metadata": assignment.get("metadata", {}),
                }
            )

    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assign_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "assignment_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


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


def build_wp_manifest(
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
    parsed_assignments = []
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

    items = []
    for assign_data in parsed_assignments:
        phase_number = assign_data["phase_number"]
        assignment_number = assign_data["assignment_number"]
        work_packages = assign_data.get("proposed_work_packages", [])
        for wp_seq, wp in enumerate(work_packages, start=1):
            wp_id = f"P{phase_number}-A{assignment_number}-WP{wp_seq}"
            est_files = wp.get("estimated_files", [])
            if not isinstance(est_files, list):
                est_files = []
            items.append(
                {
                    "id": wp_id,
                    "name": wp.get("name", ""),
                    "status": "pending",
                    "result_path": None,
                    "metadata": {
                        "scope": wp.get("scope", ""),
                        "estimated_files": est_files,
                    },
                }
            )

    manifest = {
        "pass_name": "work_packages",
        "result_dir": str(wp_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(out_dir / "wp_index.json", "[]")
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
    plan = json.loads(Path(refined_plan_path).read_text())
    phases = plan.get("phases", [])
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
        assignment_ids = [a.get("id", "") for a in previews]
        assignment_names = [a.get("name", "") for a in previews]
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
    data = json.loads(Path(refined_assignments_path).read_text())
    assignments = data.get("assignments", [])
    out_dir = Path(output_dir)
    wp_dir = out_dir / "work_packages"
    wp_dir.mkdir(parents=True, exist_ok=True)

    phase_buckets: dict[str, dict[str, object]] = {}
    for assign in assignments:
        phase_id = assign.get("phase_id", "")
        if not phase_id:
            pn = assign.get("phase_number", 0)
            phase_id = f"P{pn}"
        if phase_id not in phase_buckets:
            phase_buckets[phase_id] = {
                "id": phase_id,
                "name": assign.get("phase_name", f"Phase {phase_id}"),
                "wp_ids": [],
                "wp_names": [],
                "wp_scopes": [],
                "wp_estimated_files": [],
                "wp_count": 0,
            }
        bucket = phase_buckets[phase_id]
        wps = assign.get("proposed_work_packages", [])
        for wp in wps:
            wp_id = wp.get("id", "")
            cast_bucket_wp_ids: list[str] = bucket["wp_ids"]  # type: ignore[assignment]
            cast_bucket_wp_ids.append(wp_id)
            cast_bucket_wp_names: list[str] = bucket["wp_names"]  # type: ignore[assignment]
            cast_bucket_wp_names.append(wp.get("name", ""))
            cast_bucket_wp_scopes: list[str] = bucket["wp_scopes"]  # type: ignore[assignment]
            cast_bucket_wp_scopes.append(wp.get("scope", ""))
            est = wp.get("estimated_files", [])
            if not isinstance(est, list):
                est = []
            cast_bucket_wp_files: list[list[str]] = bucket["wp_estimated_files"]  # type: ignore[assignment]
            cast_bucket_wp_files.append(est)
            bucket["wp_count"] = int(str(bucket["wp_count"])) + 1

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
