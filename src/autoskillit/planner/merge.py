from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, write_versioned_json
from autoskillit.planner.schema import (
    ASSIGN_RESULT_FILE_RE,
    PHASE_RESULT_FILE_RE,
    WP_RESULT_FILE_RE,
    validate_phase_result,
)

logger = get_logger(__name__)

_TIER_KEYS = ("phases", "assignments", "work_packages")

_TIER_FILE_RE: dict[str, re.Pattern[str]] = {
    "phases": PHASE_RESULT_FILE_RE,
    "assignments": ASSIGN_RESULT_FILE_RE,
    "work_packages": WP_RESULT_FILE_RE,
}


def merge_files(
    file_paths: list[str],
    output_path: str,
    key: str,
    task_file_path: str = "",
    source_dir: str = "",
    strict: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    task = Path(task_file_path).read_text(encoding="utf-8") if task_file_path else ""
    if key not in _TIER_KEYS:
        raise ValueError(f"Invalid key {key!r}; must be one of {_TIER_KEYS}")

    out = Path(output_path)
    errors: list[str] = []
    skipped: int = 0
    existing_items: list[dict[str, Any]] = []

    with open(out, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            content = fh.read()
            existing: dict[str, Any] = json.loads(content) if content else {}

            existing_task = existing.get("task", task)
            existing_source_dir = existing.get("source_dir", source_dir)

            existing_items = existing.get(key, [])
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
                if item_id is None:
                    logger.debug("Skipping item with no 'id' field from %s", fp)
                    skipped += 1
                    continue
                if item_id not in existing_ids:
                    existing_items.append(item)
                    existing_ids.add(item_id)
                else:
                    logger.debug("Skipping duplicate id %r from %s", item_id, fp)
                    skipped += 1

            document: dict[str, Any] = {
                "task": existing_task,
                "source_dir": existing_source_dir,
                key: existing_items,
            }
            enriched = {**document, "schema_version": 1}
            payload = json.dumps(enriched).encode()
            fh.seek(0)
            fh.truncate()
            fh.write(payload)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    result: dict[str, Any] = {
        "merged_path": str(output_path),
        "item_count": str(len(existing_items)),
    }
    if skipped:
        result["skipped_count"] = str(skipped)
    if errors:
        result["errors"] = errors
    return result


def _write_refine_contexts(
    planner_dir: Path,
    assignments: list[dict[str, Any]],
    task_file_path: str,
) -> list[str]:
    phase_groups: dict[str, list[dict[str, Any]]] = {}
    for assignment in assignments:
        phase_id = assignment.get("phase_id", "")
        if phase_id:
            phase_groups.setdefault(phase_id, []).append(assignment)
        else:
            logger.warning(
                "Assignment %r has no phase_id — skipped from refine contexts",
                assignment.get("id", "<unknown>"),
            )

    contexts_dir = planner_dir / "refine_contexts"
    contexts_dir.mkdir(parents=True, exist_ok=True)

    context_paths: list[str] = []
    for phase_id in sorted(phase_groups):
        own = phase_groups[phase_id]
        peer_summaries: list[dict[str, str]] = [
            {"id": a.get("id", ""), "name": a.get("name", ""), "goal": a.get("goal", "")}
            for pid, peers in sorted(phase_groups.items())
            if pid != phase_id
            for a in peers
        ]
        context: dict[str, Any] = {
            "phase_id": phase_id,
            "task_file_path": task_file_path,
            "assignments": own,
            "peer_summaries": peer_summaries,
        }
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", phase_id):
            raise ValueError(
                f"phase_id {phase_id!r} contains disallowed characters — "
                "only alphanumeric, underscore, and hyphen are permitted in context filenames"
            )
        ctx_path = contexts_dir / f"context_{phase_id}.json"
        write_versioned_json(ctx_path, context, schema_version=1)
        context_paths.append(str(ctx_path))

    return context_paths


def extract_item(
    source_path: str,
    item_id: str,
    output_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    src = Path(source_path)
    try:
        data: dict[str, Any] = json.loads(src.read_text())
    except FileNotFoundError:
        raise ValueError(f"Source file not found: {source_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {source_path}: {exc}") from exc
    for tier_key in _TIER_KEYS:
        for item in data.get(tier_key, []):
            if item.get("id") == item_id:
                write_versioned_json(Path(output_path), item, schema_version=1)
                return {"extracted_path": str(output_path)}
    raise ValueError(f"Item {item_id!r} not found in {source_path}")


def replace_item(
    source_path: str,
    item_id: str,
    replacement_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        replacement: dict[str, Any] = json.loads(Path(replacement_path).read_text())
    except FileNotFoundError:
        raise ValueError(f"Replacement file not found: {replacement_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {replacement_path}: {exc}") from exc
    src = Path(source_path)

    found = False
    with open(src, "r+b") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data: dict[str, Any] = json.loads(fh.read())
            for tier_key in _TIER_KEYS:
                tier: list[dict[str, Any]] = data.get(tier_key, [])
                for idx, item in enumerate(tier):
                    if item.get("id") == item_id:
                        tier[idx] = replacement
                        enriched = {**data, "schema_version": 1}
                        payload = json.dumps(enriched).encode()
                        fh.seek(0)
                        fh.truncate()
                        fh.write(payload)
                        fh.flush()
                        found = True
                        break
                if found:
                    break
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    if not found:
        raise ValueError(f"Item {item_id!r} not found in {source_path}")
    return {"replaced_id": item_id, "updated_path": str(source_path)}


def merge_tier_results(
    results_dir: str,
    output_path: str,
    key: str,
    task_file_path: str = "",
    source_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    tier_re = _TIER_FILE_RE.get(key)
    paths = sorted(
        f
        for f in Path(results_dir).glob("*_result.json")
        if tier_re is None or tier_re.match(f.name)
    )
    if not paths:
        raise ValueError(f"No *_result.json files found in {results_dir}")
    result = merge_files(
        file_paths=[str(p) for p in paths],
        output_path=output_path,
        key=key,
        task_file_path=task_file_path,
        source_dir=source_dir,
    )
    if key == "assignments":
        merged_data = json.loads(Path(output_path).read_text(encoding="utf-8"))
        assignments = merged_data.get("assignments", [])
        if not assignments:
            logger.warning(
                "merge_tier_results: no assignments found in %s — refine contexts will be empty",
                output_path,
            )
        planner_dir = Path(output_path).parent
        context_paths = _write_refine_contexts(planner_dir, assignments, task_file_path)
        result["refine_context_paths"] = ",".join(context_paths)
    return result


def merge_refined_assignments(
    planner_dir: str,
    **kwargs: Any,
) -> dict[str, Any]:
    contexts_dir = Path(planner_dir) / "refine_contexts"
    result_files = sorted(contexts_dir.glob("*_result.json"))
    if not result_files:
        raise ValueError(
            f"No *_result.json files found in {contexts_dir}. "
            "Run refine_assignments dispatch before calling this function."
        )

    all_assignments: list[dict[str, Any]] = []
    for path in result_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping malformed result file %s: %s", path, exc)
            continue
        all_assignments.extend(data.get("assignments", []))

    valid_assignments: list[dict[str, Any]] = []
    for a in all_assignments:
        if not a.get("id"):
            logger.warning(
                "merge_refined_assignments: skipping assignment with missing id: %r",
                a.get("name", "<unknown>"),
            )
        else:
            valid_assignments.append(a)
    all_assignments = valid_assignments

    def _sort_key(assignment_id: str) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", assignment_id))

    # Single-pass: for each (file, assignment_id) claim, keep the earliest assignment_id
    file_owner: dict[str, str] = {}
    for assignment in all_assignments:
        aid = assignment.get("id", "")
        for wp in assignment.get("proposed_work_packages", []):
            for f in wp.get("files_touched", []):
                if f not in file_owner or _sort_key(aid) < _sort_key(file_owner[f]):
                    file_owner[f] = aid

    # Count conflicts: files with more than one claimant
    file_claimants: dict[str, set[str]] = {}
    for assignment in all_assignments:
        aid = assignment.get("id", "")
        for wp in assignment.get("proposed_work_packages", []):
            for f in wp.get("files_touched", []):
                file_claimants.setdefault(f, set()).add(aid)
    conflict_count = sum(1 for claimants in file_claimants.values() if len(claimants) > 1)

    # Strip files from losing assignments
    for assignment in all_assignments:
        aid = assignment.get("id", "")
        for wp in assignment.get("proposed_work_packages", []):
            wp["files_touched"] = [
                f for f in wp.get("files_touched", []) if file_owner.get(f) == aid
            ]

    output_path = Path(planner_dir) / "refined_assignments.json"
    write_versioned_json(output_path, {"assignments": all_assignments}, schema_version=1)

    return {
        "refined_assignments_path": str(output_path),
        "item_count": str(len(all_assignments)),
        "conflict_count": str(conflict_count),
    }


def build_plan_snapshot(
    phases_dir: str,
    output_path: str,
    task_file_path: str = "",
    source_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    task = Path(task_file_path).read_text(encoding="utf-8") if task_file_path else ""
    phase_pairs: list[tuple[int, dict[str, Any]]] = []
    for p in sorted(
        f for f in Path(phases_dir).glob("*_result.json") if PHASE_RESULT_FILE_RE.match(f.name)
    ):
        try:
            raw = json.loads(p.read_text())
            validated = validate_phase_result(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skipping malformed phase file %s: %s", p, exc)
            continue
        short: dict[str, Any] = {
            "id": validated["id"],
            "name": validated["name"],
            "goal": validated.get("goal", ""),
            "scope": validated.get("scope", []),
            "ordering": validated["ordering"],
        }
        phase_pairs.append((int(validated["ordering"]), short))

    phase_pairs.sort(key=lambda x: x[0])
    phases = [pair[1] for pair in phase_pairs]

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
