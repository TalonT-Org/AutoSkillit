from __future__ import annotations

import json
import secrets
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from autoskillit.core import atomic_write, get_logger, read_versioned_json, write_versioned_json
from autoskillit.planner._sort_utils import _natural_sort_key
from autoskillit.planner.lifecycle import (
    LifecycleCategory,
    load_lifecycle_registry,
    record_lifecycle_event,
)
from autoskillit.planner.schema import (
    ASSIGN_ID_RE,
    ASSIGN_RESULT_FILE_RE,
    PHASE_RESULT_FILE_RE,
    WP_RESULT_FILE_RE,
    RunDirResult,
    TaskResolutionResult,
    make_canonical_assignment_id,
    validate_assignment_result,
    validate_phase_result,
    validate_refined_assignments,
    validate_refined_plan,
    validate_wp_result,
)
from autoskillit.planner.validation import discover_tier_files

logger = get_logger(__name__)


def _status_from_content(data: dict) -> str:
    if data.get("elaboration_failed"):
        return "elaboration_failed"
    return "done"


class _PhaseBucket(TypedDict):
    id: str
    name: str
    wp_ids: list[str]
    wp_names: list[str]
    wp_scopes: list[str]
    wp_estimated_files: list[list[str]]
    wp_count: int


def _ensure_sentinel_dir(tier_dir: Path, sentinel_name: str) -> Path:
    sentinel_dir = tier_dir / sentinel_name
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    return sentinel_dir


def _capture_head_sha(source_dir: str) -> str:
    if not source_dir:
        return ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if len(sha) == 40:
                return sha
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def create_run_dir(temp_dir: str, source_dir: str = "") -> RunDirResult:
    if not temp_dir:
        raise ValueError("temp_dir must be a non-empty path")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(temp_dir) / "planner" / f"run-{stamp}-{secrets.token_hex(4)}"
    for sub in ("phases", "assignments", "work_packages"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    plan_id = str(uuid.uuid4())
    source_commit = _capture_head_sha(source_dir)
    return RunDirResult(planner_dir=str(run_dir), plan_id=plan_id, source_commit=source_commit)


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

    discovery = discover_tier_files(phases_path, PHASE_RESULT_FILE_RE)
    for f in discovery.rejected:
        logger.warning("phase file %s does not match phase naming pattern", f.name)
    phase_files = discovery.accepted
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

    sentinel_dir = _ensure_sentinel_dir(assign_dir, "assign_sentinels")
    manifest = {
        "pass_name": "phase_assignments",
        "result_dir": str(sentinel_dir),
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

    discovery = discover_tier_files(assign_path, ASSIGN_RESULT_FILE_RE)
    for f in discovery.rejected:
        logger.warning("assignment file %s does not match assignment naming pattern", f.name)
    assign_files = discovery.accepted
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

    sentinel_dir = _ensure_sentinel_dir(wp_dir, "wp_sentinels")

    manifest = {
        "pass_name": "phase_work_packages",
        "result_dir": str(sentinel_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "phase_wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(wp_dir / "wp_index.json", "[]")
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


def finalize_wp_manifest(work_packages_dir: str, output_dir: str) -> dict[str, str]:
    if not work_packages_dir or not output_dir:
        raise ValueError("work_packages_dir and output_dir must not be empty")

    wp_dir = Path(work_packages_dir)
    if not wp_dir.is_dir():
        raise FileNotFoundError(f"work_packages_dir does not exist: {wp_dir}")

    discovery = discover_tier_files(wp_dir, WP_RESULT_FILE_RE)
    for f in discovery.rejected:
        logger.warning("work package file %s does not match WP naming pattern", f.name)
    result_files = sorted(discovery.accepted, key=lambda p: _natural_sort_key(p.name))
    items = []
    index_entries = []
    errors: list[str] = []
    for f in result_files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        try:
            data = validate_wp_result(raw, allow_stub=bool(raw.get("elaboration_failed")))
        except (ValueError, KeyError) as exc:
            errors.append(f"{f.name}: {exc}")
            continue
        items.append(
            {
                "id": data["id"],
                "name": data["name"],
                "status": _status_from_content(data),
                "result_path": str(f),
                "metadata": {},
            }
        )
        index_entries.append(_build_index_entry(data))

    if errors:
        detail = "; ".join(errors)
        raise ValueError(
            f"{len(errors)} WP validation error{'s' if len(errors) != 1 else ''}: {detail}"
        )

    items.sort(key=lambda i: _natural_sort_key(str(i["id"])))
    index_entries.sort(key=lambda e: _natural_sort_key(str(e["id"])))

    manifest = {
        "pass_name": "work_packages",
        "result_dir": str(wp_dir.resolve()),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = wp_dir / "wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(wp_dir / "wp_index.json", json.dumps(index_entries, indent=2))
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
    task_file_path: str = str(kwargs.get("task_file_path") or "")
    assign_dir = Path(output_dir) / "assignments"

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
                if not aid or not ASSIGN_ID_RE.match(aid):
                    aid = make_canonical_assignment_id(phase_id, idx)
                assignment_ids.append(aid)
            else:
                assignment_ids.append(make_canonical_assignment_id(phase_id, idx))
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
            "task_file_path": task_file_path,
            "metadata": metadata,
            "prior_results": [],
        }
        ctx_path = assign_dir / f"context_{phase_id}.json"
        write_versioned_json(ctx_path, context, schema_version=1)
        context_paths.append(str(ctx_path))
        item_ids.append(phase_id)

    voided_phase_ids = [phase["id"] for phase in phases if not phase.get("assignments_preview")]
    if voided_phase_ids:
        record_lifecycle_event(Path(output_dir), LifecycleCategory.VOIDED_PHASES, voided_phase_ids)

    sentinel_dir = _ensure_sentinel_dir(assign_dir, "assign_sentinels")
    manifest = {
        "pass_name": "phase_assignments",
        "result_dir": str(sentinel_dir),
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
    task_file_path: str = str(kwargs.get("task_file_path") or "")
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
            "task_file_path": task_file_path,
            "metadata": metadata,
            "prior_results": [],
            "wp_index_path": str(wp_dir / "wp_index.json"),
        }
        ctx_path = wp_dir / f"context_{phase_id}.json"
        write_versioned_json(ctx_path, context, schema_version=1)
        context_paths.append(str(ctx_path))
        item_ids.append(str(phase_id))

    sentinel_dir = _ensure_sentinel_dir(wp_dir, "wp_sentinels")
    manifest = {
        "pass_name": "phase_work_packages",
        "result_dir": str(sentinel_dir),
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "phase_wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(wp_dir / "wp_index.json", "[]")
    return {
        "manifest_path": str(manifest_path),
        "context_paths": ",".join(context_paths),
        "item_ids": ",".join(item_ids),
    }


def _derive_label(content: str, filename_stem: str) -> str:
    first_non_empty: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if first_non_empty is None and stripped:
            first_non_empty = stripped[:80]
    return first_non_empty or filename_stem or "Untitled"


def resolve_task_input(task: str, planner_dir: str) -> TaskResolutionResult:
    if not task:
        raise ValueError("task must be a non-empty string")
    task_path = Path(task)
    if task_path.is_file():
        try:
            content = task_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Cannot read task file {task_path}: {exc}") from exc
        label = _derive_label(content, task_path.stem)
        return TaskResolutionResult(task_file_path=str(task_path), task_label=label)
    out = Path(planner_dir) / "task_input.md"
    atomic_write(out, task)
    label = _derive_label(task, "")
    return TaskResolutionResult(task_file_path=str(out), task_label=label)


def reconcile_wp_files(planner_dir: str) -> dict[str, str]:
    planner_path = Path(planner_dir)
    wp_dir = planner_path / "work_packages"
    if not wp_dir.is_dir():
        return {"archived_count": "0", "archived_ids": ""}

    active_ids: set[str] = set()
    found_but_unreadable: list[str] = []
    for candidate in ("consolidated_wps.json", "refined_wps.json"):
        candidate_path = planner_path / candidate
        if candidate_path.exists():
            doc = read_versioned_json(candidate_path, 1)
            if doc:
                active_ids = {wp["id"] for wp in doc.get("work_packages", [])}
                break
            found_but_unreadable.append(candidate)
    else:
        if found_but_unreadable:
            logger.warning(
                "reconcile_wp_files: WP files exist but are unreadable",
                unreadable=found_but_unreadable,
            )
        else:
            logger.warning("reconcile_wp_files: no consolidated or refined WPs file found")
        return {"archived_count": "0", "archived_ids": ""}

    registry = load_lifecycle_registry(planner_path)
    excluded_ids = set(registry.get("absorbed", {}).keys()) | set(
        registry.get("voided_wps", {}).keys()
    )

    disk_ids: dict[str, Path] = {}
    for f in wp_dir.glob("*_result.json"):
        if f.parent != wp_dir:
            continue
        stem = f.name.removesuffix("_result.json")
        if WP_RESULT_FILE_RE.match(f.name):
            disk_ids[stem] = f

    orphan_ids = {wid for wid in disk_ids if wid not in active_ids and wid not in excluded_ids}
    if not orphan_ids:
        return {"archived_count": "0", "archived_ids": ""}

    archived_dir = wp_dir / "archived"
    archived_dir.mkdir(exist_ok=True)
    moved_ids: list[str] = []
    for orphan_id in sorted(orphan_ids):
        src = disk_ids[orphan_id]
        try:
            src.rename(archived_dir / src.name)
            moved_ids.append(orphan_id)
        except OSError:
            logger.warning("reconcile_wp_files: rename failed", orphan_id=orphan_id)

    if moved_ids:
        record_lifecycle_event(
            planner_path,
            LifecycleCategory.ARCHIVED_STUBS,
            {oid: {"reason": "elaboration_failed_orphan"} for oid in moved_ids},
        )
    logger.info("reconcile_wp_files", archived_count=len(moved_ids))
    return {
        "archived_count": str(len(moved_ids)),
        "archived_ids": ",".join(sorted(moved_ids)),
    }
