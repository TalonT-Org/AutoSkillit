from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, write_versioned_json

_logger = get_logger(__name__)


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

    for item in items:
        if item["status"] == "processing":
            result_path = out_dir / f"{item['id']}_result.json"
            if result_path.exists():
                item["status"] = "done"
                item["result_path"] = str(result_path)
                if pass_name == "work_packages":
                    _backstop_wp_index(item["id"], result_path, out_dir)
            else:
                _logger.warning(
                    "check_remaining: no result file — marking failed",
                    item_id=item["id"],
                    result_path=str(result_path),
                )
                item["status"] = "failed"

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
        return {"current_item_path": str(context_path), "has_remaining": "true"}

    write_versioned_json(manifest_file, manifest, schema_version=1)
    return {"current_item_path": "", "has_remaining": "false"}


def build_assignment_manifest(
    phases_dir: str, assignments_dir: str, output_dir: str
) -> dict[str, str]:
    if not phases_dir or not output_dir:
        raise ValueError("phases_dir and output_dir must not be empty")

    phases_path = Path(phases_dir)
    out_dir = Path(output_dir)

    phase_files = sorted(phases_path.glob("*_result.json"))
    parsed_phases = []
    for f in phase_files:
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        pn = data.get("phase_number", 0)
        if not isinstance(pn, int):
            raise TypeError(
                f"phase_number in {f} must be an integer, got {type(pn).__name__}: {pn!r}"
            )
        parsed_phases.append(data)
    parsed_phases.sort(key=lambda d: d.get("phase_number", 0))

    items = []
    for phase_data in parsed_phases:
        phase_number = phase_data.get("phase_number", 0)
        assignments = phase_data.get("assignments", [])
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
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "assignment_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}


def build_wp_manifest(assignments_dir: str, output_dir: str) -> dict[str, str]:
    if not assignments_dir or not output_dir:
        raise ValueError("assignments_dir and output_dir must not be empty")

    assign_path = Path(assignments_dir)
    out_dir = Path(output_dir)

    assign_files = list(assign_path.glob("*_result.json"))
    parsed_assignments = []
    for f in assign_files:
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            raise json.JSONDecodeError(
                f"Failed to parse {f}: {exc.msg}", exc.doc, exc.pos
            ) from exc
        parsed_assignments.append(data)
    parsed_assignments.sort(
        key=lambda d: (d.get("phase_number", 0), d.get("assignment_number", 0))
    )

    items = []
    for assign_data in parsed_assignments:
        phase_number = assign_data.get("phase_number", 0)
        assignment_number = assign_data.get("assignment_number", 0)
        work_packages = assign_data.get("proposed_work_packages", [])
        for wp_seq, wp in enumerate(work_packages, start=1):
            wp_id = f"P{phase_number}-A{assignment_number}-WP{wp_seq}"
            items.append(
                {
                    "id": wp_id,
                    "name": wp.get("name", ""),
                    "status": "pending",
                    "result_path": None,
                    "metadata": {
                        "scope": wp.get("scope", ""),
                        "estimated_files": wp.get("estimated_files", []),
                    },
                }
            )

    manifest = {
        "pass_name": "work_packages",
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    manifest_path = out_dir / "wp_manifest.json"
    write_versioned_json(manifest_path, manifest, schema_version=1)
    atomic_write(out_dir / "wp_index.json", "[]")
    return {"manifest_path": str(manifest_path), "total_count": str(len(items))}
