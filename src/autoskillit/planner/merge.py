from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write, get_logger, write_versioned_json

_logger = get_logger(__name__)

_TIER_KEYS = ("phases", "assignments", "work_packages")


def merge_files(
    file_paths: list[str],
    output_path: str,
    key: str,
    task: str = "",
    source_dir: str = "",
    strict: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    out = Path(output_path)
    errors: list[str] = []

    existing: dict[str, Any] = {}
    if out.exists():
        existing = json.loads(out.read_text())

    existing_task = existing.get("task", task)
    existing_source_dir = existing.get("source_dir", source_dir)

    existing_items: list[dict[str, Any]] = existing.get(key, [])
    existing_ids: set[str] = {item["id"] for item in existing_items if "id" in item}

    for fp in file_paths:
        p = Path(fp)
        if not p.exists():
            msg = f"File not found: {fp}"
            if strict:
                raise ValueError(msg)
            errors.append(msg)
            continue
        try:
            item = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON in {fp}: {exc}"
            if strict:
                raise ValueError(msg) from exc
            errors.append(msg)
            continue
        item_id = item.get("id")
        if item_id not in existing_ids:
            existing_items.append(item)
            if item_id is not None:
                existing_ids.add(item_id)

    document: dict[str, Any] = {
        "task": existing_task,
        "source_dir": existing_source_dir,
        key: existing_items,
    }
    write_versioned_json(out, document, schema_version=1)

    result: dict[str, Any] = {
        "merged_path": str(output_path),
        "item_count": str(len(existing_items)),
    }
    if errors:
        result["errors"] = errors
    return result


def extract_item(
    source_path: str,
    item_id: str,
    output_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(source_path).read_text())
    for tier_key in _TIER_KEYS:
        for item in data.get(tier_key, []):
            if item.get("id") == item_id:
                atomic_write(Path(output_path), json.dumps(item))
                return {"extracted_path": str(output_path)}
    raise ValueError(f"Item {item_id!r} not found in {source_path}")


def replace_item(
    source_path: str,
    item_id: str,
    replacement_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    replacement: dict[str, Any] = json.loads(Path(replacement_path).read_text())
    src = Path(source_path)

    found = False
    with open(src, "rb") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data: dict[str, Any] = json.loads(src.read_text())
            for tier_key in _TIER_KEYS:
                tier: list[dict[str, Any]] = data.get(tier_key, [])
                for idx, item in enumerate(tier):
                    if item.get("id") == item_id:
                        tier[idx] = replacement
                        write_versioned_json(src, data, schema_version=1)
                        found = True
                        break
                if found:
                    break
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    if not found:
        raise ValueError(f"Item {item_id!r} not found in {source_path}")
    return {"replaced_id": item_id, "updated_path": str(source_path)}


def build_plan_snapshot(
    phases_dir: str,
    output_path: str,
    task: str = "",
    source_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    from autoskillit.planner.schema import validate_phase_result

    phases: list[dict[str, Any]] = []
    for p in sorted(Path(phases_dir).glob("*_result.json")):
        try:
            raw = json.loads(p.read_text())
            validated = validate_phase_result(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            _logger.warning("Skipping malformed phase file %s: %s", p, exc)
            continue
        short: dict[str, Any] = {
            "id": validated["id"],
            "name": validated["name"],
            "goal": validated.get("goal", ""),
            "scope": validated.get("scope", []),
        }
        phases.append(short)

    phases.sort(key=lambda ph: int(str(ph["id"])[1:]))

    document: dict[str, Any] = {
        "task": task,
        "source_dir": source_dir,
        "phases": phases,
    }
    write_versioned_json(Path(output_path), document, schema_version=1)

    return {
        "snapshot_path": str(output_path),
        "phase_ids": ",".join(ph["id"] for ph in phases),
    }
