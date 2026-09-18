from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any, BinaryIO

import regex as re

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    VANISHED_ERRORS,
    acquire_flock_with_timeout,
    get_logger,
    write_versioned_json,
)
from autoskillit.planner.lifecycle import LifecycleCategory, record_lifecycle_event
from autoskillit.planner.schema import (
    ASSIGN_RESULT_FILE_RE,
    PHASE_RESULT_FILE_RE,
    WP_RESULT_FILE_RE,
    collect_tier_result_files,
    validate_phase_result,
)
from autoskillit.planner.validation import discover_tier_files

logger = get_logger(__name__)

_TIER_KEYS = ("phases", "assignments", "work_packages")
_PLANNER_MERGE_LOCK_TIMEOUT_SECONDS = ARTIFACT_LEASE_TIMEOUT_SECONDS

_TIER_FILE_RE: dict[str, re.Pattern[str]] = {
    "phases": PHASE_RESULT_FILE_RE,
    "assignments": ASSIGN_RESULT_FILE_RE,
    "work_packages": WP_RESULT_FILE_RE,
}


def _merge_files_locked_transaction(
    fh: BinaryIO,
    file_paths: list[str],
    key: str,
    task: str,
    source_dir: str,
    strict: bool,
    skip_vanished_results: bool,
) -> tuple[list[dict[str, Any]], int, list[str], list[str]]:
    fh.seek(0)
    content = fh.read()
    existing: dict[str, Any] = json.loads(content) if content else {}

    existing_task = existing.get("task", task)
    existing_source_dir = existing.get("source_dir", source_dir)
    existing_items: list[dict[str, Any]] = existing.get(key, [])
    existing_ids = {item["id"] for item in existing_items if "id" in item}
    errors: list[str] = []
    skipped = 0
    skipped_result_files: list[str] = []

    for fp in file_paths:
        path = Path(fp)
        try:
            item = json.loads(path.read_text())
        except VANISHED_ERRORS as exc:
            msg = f"File not found: {fp}"
            if skip_vanished_results:
                skipped_result_files.append(path.name)
                continue
            if strict:
                raise ValueError(msg) from exc
            errors.append(msg)
            continue
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
    payload = json.dumps({**document, "schema_version": 1}, indent=2).encode()
    fh.seek(0)
    fh.truncate()
    fh.write(payload)
    fh.flush()
    return existing_items, skipped, skipped_result_files, errors


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
    skip_vanished_results = bool(kwargs.get("_skip_vanished_results"))

    with open(out, "a+b") as fh:
        acquire_flock_with_timeout(
            fh.fileno(),
            operation=fcntl.LOCK_EX,
            timeout=_PLANNER_MERGE_LOCK_TIMEOUT_SECONDS,
            path=out,
        )
        try:
            existing_items, skipped, skipped_result_files, errors = (
                _merge_files_locked_transaction(
                    fh,
                    file_paths,
                    key,
                    task,
                    source_dir,
                    strict,
                    skip_vanished_results,
                )
            )
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    result: dict[str, Any] = {
        "merged_path": str(output_path),
        "item_count": str(len(existing_items)),
    }
    if skipped:
        result["skipped_count"] = str(skipped)
    if skipped_result_files:
        result["skipped_result_files"] = skipped_result_files
    if errors:
        result["errors"] = errors
    return result


def _write_refine_contexts(
    planner_dir: Path,
    assignments: list[dict[str, Any]],
    task_file_path: str,
    expected_phase_ids: frozenset[str] | None = None,
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

    if expected_phase_ids is not None:
        missing = expected_phase_ids - frozenset(phase_groups)
        if missing:
            raise ValueError(
                f"Phases {sorted(missing)} have no merged assignments — "
                f"expected={sorted(expected_phase_ids)}, "
                f"found={sorted(phase_groups)}"
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


def _write_wp_refine_contexts(
    planner_dir: Path,
    work_packages: list[dict[str, Any]],
    task_file_path: str,
    expected_phase_ids: frozenset[str] | None = None,
) -> list[str]:
    phase_groups: dict[str, list[dict[str, Any]]] = {}
    for wp in work_packages:
        phase_id = wp.get("phase_id", "")
        if phase_id:
            phase_groups.setdefault(phase_id, []).append(wp)
        else:
            logger.warning(
                "WP %r has no phase_id — skipped from wp refine contexts",
                wp.get("id", "<unknown>"),
            )

    if expected_phase_ids is not None:
        missing = expected_phase_ids - frozenset(phase_groups)
        if missing:
            raise ValueError(
                f"Phases {sorted(missing)} have no merged work packages — "
                f"expected={sorted(expected_phase_ids)}, "
                f"found={sorted(phase_groups)}"
            )

    contexts_dir = planner_dir / "wp_refine_contexts"
    try:
        contexts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Failed to create wp_refine_contexts directory at {contexts_dir}: {exc}"
        ) from exc

    _WP_PEER_STUB_KEYS = frozenset(
        {"id", "name", "scope", "deliverables", "apis_defined", "apis_consumed"}
    )

    context_paths: list[str] = []
    for phase_id in sorted(phase_groups):
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", phase_id):
            raise ValueError(
                f"phase_id {phase_id!r} contains disallowed characters — "
                "only alphanumeric, underscore, and hyphen are permitted in context filenames"
            )
        own = phase_groups[phase_id]
        peer_summaries: list[dict[str, Any]] = [
            {k: v for k, v in wp.items() if k in _WP_PEER_STUB_KEYS}
            for pid, peers in sorted(phase_groups.items())
            if pid != phase_id
            for wp in peers
        ]
        context: dict[str, Any] = {
            "phase_id": phase_id,
            "task_file_path": task_file_path,
            "work_packages": own,
            "peer_summaries": peer_summaries,
        }
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
            acquire_flock_with_timeout(
                fh.fileno(),
                operation=fcntl.LOCK_EX,
                timeout=_PLANNER_MERGE_LOCK_TIMEOUT_SECONDS,
                path=src,
            )
            data: dict[str, Any] = json.loads(fh.read())
            for tier_key in _TIER_KEYS:
                tier: list[dict[str, Any]] = data.get(tier_key, [])
                for idx, item in enumerate(tier):
                    if item.get("id") == item_id:
                        tier[idx] = replacement
                        enriched = {**data, "schema_version": 1}
                        payload = json.dumps(enriched, indent=2).encode()
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


def _discover_tier_result_files(results_dir: str, key: str) -> tuple[list[Path], dict[str, bool]]:
    tier_re = _TIER_FILE_RE.get(key)
    if tier_re is not None:
        return collect_tier_result_files(Path(results_dir), tier_re), {
            "_skip_vanished_results": True
        }
    # No canonical regex defined for this key — accept all *_result.json files without
    # tier validation. This is intentional: unknown key types have no naming constraint.
    return sorted(Path(results_dir).glob("*_result.json")), {}


def _postprocess_merged_assignments(
    result: dict[str, Any],
    output_path: str,
    results_dir: str,
    task_file_path: str,
) -> None:
    merged_data = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assignments = merged_data.get("assignments", [])
    if not assignments:
        logger.warning(
            "merge_tier_results: no assignments found in %s — refine contexts will be empty",
            output_path,
        )
    planner_dir = Path(output_path).parent
    expected_phase_ids: frozenset[str] | None = None
    manifest_path = Path(results_dir) / "phase_assignment_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Corrupted phase_assignment_manifest.json at {manifest_path}: {exc}"
            ) from exc
        manifest_items = manifest.get("items", [])
        if any(not item.get("id") for item in manifest_items):
            raise ValueError(f"Manifest item has missing or empty 'id' field at {manifest_path}")
        expected_phase_ids = frozenset(item["id"] for item in manifest_items)
    context_paths = _write_refine_contexts(
        planner_dir, assignments, task_file_path, expected_phase_ids=expected_phase_ids
    )
    result["refine_context_paths"] = ",".join(context_paths)


def _postprocess_merged_work_packages(
    result: dict[str, Any], output_path: str, task_file_path: str
) -> None:
    try:
        merged_data = json.loads(Path(output_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(f"Failed to read merged output at {output_path}: {exc}") from exc
    work_packages = merged_data.get("work_packages", [])
    if not work_packages:
        logger.warning(
            "merge_tier_results: no work_packages found in %s — wp refine contexts will be empty",
            output_path,
        )
    planner_dir = Path(output_path).parent
    expected_phase_ids: frozenset[str] | None = None
    manifest_path = planner_dir / "phase_wp_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Corrupted phase_wp_manifest.json at {manifest_path}: {exc}"
            ) from exc
        except OSError as exc:
            raise ValueError(f"Cannot read {manifest_path}: {exc}") from exc
        manifest_items = manifest.get("items", [])
        if any(not item.get("id") for item in manifest_items):
            raise ValueError(f"Manifest item has missing or empty 'id' field at {manifest_path}")
        expected_phase_ids = frozenset(item["id"] for item in manifest_items)
    context_paths = _write_wp_refine_contexts(
        planner_dir, work_packages, task_file_path, expected_phase_ids=expected_phase_ids
    )
    result["wp_refine_context_paths"] = ",".join(context_paths)


def merge_tier_results(
    results_dir: str,
    output_path: str,
    key: str,
    task_file_path: str = "",
    source_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    paths, merge_kwargs = _discover_tier_result_files(results_dir, key)
    if not paths:
        raise ValueError(f"No *_result.json files found in {results_dir}")
    result = merge_files(
        file_paths=[str(p) for p in paths],
        output_path=output_path,
        key=key,
        task_file_path=task_file_path,
        source_dir=source_dir,
        **merge_kwargs,
    )
    if key == "assignments":
        _postprocess_merged_assignments(result, output_path, results_dir, task_file_path)
    elif key == "work_packages":
        _postprocess_merged_work_packages(result, output_path, task_file_path)
    return result


def _read_refined_result_items(
    contexts_dir: Path,
    item_key: str,
    merge_name: str,
    item_name: str,
    dispatch_name: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    result_files = sorted(contexts_dir.glob("*_result.json"))
    if not result_files:
        raise ValueError(
            f"No *_result.json files found in {contexts_dir}. "
            f"Run {dispatch_name} dispatch before calling this function."
        )

    all_items: list[dict[str, Any]] = []
    skipped_result_files: list[str] = []
    for path in result_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed result file %s: %s", path, exc)
            continue
        except VANISHED_ERRORS:
            skipped_result_files.append(path.name)
            continue
        all_items.extend(data.get(item_key, []))

    valid_items: list[dict[str, Any]] = []
    for item in all_items:
        if not item.get("id"):
            logger.warning(
                f"{merge_name}: skipping {item_name} with missing id: %r",
                item.get("name", "<unknown>"),
            )
        else:
            valid_items.append(item)
    return valid_items, skipped_result_files, len(result_files)


def _numeric_planner_id_sort_key(item_id: str) -> tuple[int, ...]:
    return tuple(int(number) for number in re.findall(r"\d+", item_id))


def _resolve_exclusive_claims(
    items: list[dict[str, Any]], claim_key: str, child_key: str | None = None
) -> tuple[dict[str, str], int]:
    claim_owner: dict[str, str] = {}
    claimants: dict[str, set[str]] = {}
    for item in items:
        item_id = item.get("id", "")
        claim_sources = item.get(child_key, []) if child_key else (item,)
        for source in claim_sources:
            for claim in source.get(claim_key, []):
                owner = claim_owner.get(claim)
                if owner is None or _numeric_planner_id_sort_key(item_id) < (
                    _numeric_planner_id_sort_key(owner)
                ):
                    claim_owner[claim] = item_id
                claimants.setdefault(claim, set()).add(item_id)
    conflict_count = sum(1 for claimant_ids in claimants.values() if len(claimant_ids) > 1)
    return claim_owner, conflict_count


def merge_refined_assignments(
    planner_dir: str,
    **kwargs: Any,
) -> dict[str, Any]:
    contexts_dir = Path(planner_dir) / "refine_contexts"
    all_assignments, skipped_result_files, _ = _read_refined_result_items(
        contexts_dir,
        "assignments",
        "merge_refined_assignments",
        "assignment",
        "refine_assignments",
    )
    file_owner, conflict_count = _resolve_exclusive_claims(
        all_assignments, "files_touched", child_key="proposed_work_packages"
    )

    # Strip files from losing assignments
    for assignment in all_assignments:
        aid = assignment.get("id", "")
        for wp in assignment.get("proposed_work_packages", []):
            wp["files_touched"] = [
                f for f in wp.get("files_touched", []) if file_owner.get(f) == aid
            ]
            if not wp["files_touched"]:
                logger.warning(
                    "WP %s in assignment %s has empty files_touched after conflict resolution",
                    wp.get("id", wp.get("name", "<unknown>")),
                    aid,
                )

    voided_ids = [a["id"] for a in all_assignments if not a.get("proposed_work_packages")]
    if voided_ids:
        record_lifecycle_event(Path(planner_dir), LifecycleCategory.VOIDED_ASSIGNMENTS, voided_ids)

    output_path = Path(planner_dir) / "refined_assignments.json"
    write_versioned_json(output_path, {"assignments": all_assignments}, schema_version=1)

    result: dict[str, Any] = {
        "refined_assignments_path": str(output_path),
        "item_count": str(len(all_assignments)),
        "conflict_count": str(conflict_count),
    }
    if skipped_result_files:
        result["skipped_result_files"] = skipped_result_files
    return result


def merge_refined_wps(
    planner_dir: str,
    **kwargs: Any,
) -> dict[str, Any]:
    contexts_dir = Path(planner_dir) / "wp_refine_contexts"
    all_wps, skipped_result_files, result_file_count = _read_refined_result_items(
        contexts_dir,
        "work_packages",
        "merge_refined_wps",
        "wp",
        "refine_wps",
    )

    if not all_wps:
        logger.warning(
            "merge_refined_wps: no valid WPs collected from %d result file(s) in %s",
            result_file_count,
            contexts_dir,
        )

    deliverable_owner, conflict_count = _resolve_exclusive_claims(all_wps, "deliverables")

    for wp in all_wps:
        wid = wp.get("id", "")
        wp["deliverables"] = [
            d for d in wp.get("deliverables", []) if deliverable_owner.get(d) == wid
        ]

    output_path = Path(planner_dir) / "refined_wps.json"
    write_versioned_json(output_path, {"work_packages": all_wps}, schema_version=1)

    result: dict[str, Any] = {
        "refined_wps_path": str(output_path),
        "item_count": str(len(all_wps)),
        "conflict_count": str(conflict_count),
    }
    if skipped_result_files:
        result["skipped_result_files"] = skipped_result_files
    return result


def build_plan_snapshot(
    phases_dir: str,
    output_path: str,
    task_file_path: str = "",
    source_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    task = Path(task_file_path).read_text(encoding="utf-8") if task_file_path else ""
    phases_path = Path(phases_dir)
    discovery = discover_tier_files(phases_path, PHASE_RESULT_FILE_RE)
    for p in discovery.rejected:
        logger.warning("phase file %s does not match phase naming pattern", p.name)
    phase_pairs: list[tuple[int, dict[str, Any]]] = []
    for p in sorted(discovery.accepted):
        try:
            raw = json.loads(p.read_text())
            validated = validate_phase_result(raw)
        except VANISHED_ERRORS:
            continue
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
