"""WP consolidation pass: merges work packages per manifests with file-sharing fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write, write_versioned_json
from autoskillit.planner.schema import validate_wp_result

_ASSIGNMENT_RE = re.compile(r"^(P\d+-A\d+)-")
_FALLBACK_MIN_WPS = 5
_FALLBACK_MAX_GROUP_SIZE = 5


def _natural_sort_key(s: str) -> list[int | str]:
    parts = re.split(r"(\d+)", s)
    return [int(p) if p.isdigit() else p for p in parts]


@dataclass(frozen=True)
class _ConsolidationGroup:
    merged_id: str
    source_wp_ids: list[str]
    merge_order: list[str]
    name: str | None
    goal: str | None


def _load_manifests(consolidation_dir: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in sorted(consolidation_dir.glob("*_consolidation.json")):
        try:
            manifests.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"failed to load consolidation manifest {path}: {exc}") from exc
    return manifests


def _build_group_maps(
    manifests: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, _ConsolidationGroup]]:
    source_to_merged: dict[str, str] = {}
    merged_groups: dict[str, _ConsolidationGroup] = {}
    for manifest_idx, manifest in enumerate(manifests):
        for group_idx, g in enumerate(manifest.get("groups", [])):
            try:
                merged_id = g["merged_id"]
                source_wp_ids = g["source_wp_ids"]
            except KeyError as exc:
                raise ValueError(
                    f"malformed group at manifest[{manifest_idx}]"
                    f".groups[{group_idx}]: missing key {exc}"
                ) from exc
            if merged_id in merged_groups:
                raise ValueError(
                    f"duplicate merged_id {merged_id!r} across consolidation manifests"
                )
            group = _ConsolidationGroup(
                merged_id=merged_id,
                source_wp_ids=source_wp_ids,
                merge_order=g.get("merge_order", source_wp_ids),
                name=g.get("name"),
                goal=g.get("goal"),
            )
            merged_groups[group.merged_id] = group
            for src_id in group.source_wp_ids:
                source_to_merged[src_id] = group.merged_id
    return source_to_merged, merged_groups


def _merge_group(
    group: _ConsolidationGroup,
    wp_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    primary = wp_by_id[group.merged_id]
    sources_in_order = [wp_by_id[id_] for id_ in group.merge_order]

    merged = dict(primary)
    merged["name"] = group.name or primary["name"]
    merged["goal"] = group.goal or primary["goal"]
    merged["technical_steps"] = [
        s for wp in sources_in_order for s in wp.get("technical_steps", [])
    ]

    for field in (
        "deliverables",
        "acceptance_criteria",
        "files_touched",
        "apis_defined",
        "apis_consumed",
    ):
        seen: set[str] = set()
        merged[field] = [
            x
            for wp in sources_in_order
            for x in wp.get(field, [])
            if not (x in seen or seen.add(x))  # type: ignore[func-returns-value]
        ]

    # Collect all external deps; intra-group removal done in rewrite pass
    all_source_ids = set(group.source_wp_ids)
    raw_deps: list[str] = []
    seen_deps: set[str] = set()
    for wp in sources_in_order:
        for dep in wp.get("depends_on", []):
            if dep not in all_source_ids and dep not in seen_deps:
                raw_deps.append(dep)
                seen_deps.add(dep)
    merged["depends_on"] = raw_deps

    return merged


def _rewrite_deps(
    wp: dict[str, Any],
    source_to_merged: dict[str, str],
    own_group_sources: set[str],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for dep in wp.get("depends_on", []):
        if dep in own_group_sources:
            continue
        canonical = source_to_merged.get(dep, dep)
        if canonical not in seen:
            result.append(canonical)
            seen.add(canonical)
    return result


def _cluster_by_shared_files(wps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster WPs that share at least one files_touched entry."""
    parent: dict[str, str] = {wp["id"]: wp["id"] for wp in wps}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    file_to_wps: dict[str, list[str]] = {}
    for wp in wps:
        for f in wp.get("files_touched", []):
            file_to_wps.setdefault(f, []).append(wp["id"])

    for wp_ids in file_to_wps.values():
        for i in range(1, len(wp_ids)):
            union(wp_ids[0], wp_ids[i])

    clusters: dict[str, list[dict[str, Any]]] = {}
    for wp in wps:
        root = find(wp["id"])
        clusters.setdefault(root, []).append(wp)

    return list(clusters.values())


def _chunk_list(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_fallback_groups(
    work_packages: list[dict[str, Any]],
) -> list[_ConsolidationGroup]:
    """Heuristic fallback: merge same-assignment WPs sharing files."""
    if len(work_packages) < _FALLBACK_MIN_WPS:
        return []

    by_assignment: dict[str, list[dict[str, Any]]] = {}
    for wp in work_packages:
        m = _ASSIGNMENT_RE.match(wp["id"])
        if m:
            by_assignment.setdefault(m.group(1), []).append(wp)

    groups: list[_ConsolidationGroup] = []
    for wps in by_assignment.values():
        if len(wps) < 2:
            continue
        clusters = _cluster_by_shared_files(wps)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            for chunk in _chunk_list(cluster, _FALLBACK_MAX_GROUP_SIZE):
                if len(chunk) < 2:
                    continue
                sorted_ids = sorted([wp["id"] for wp in chunk], key=_natural_sort_key)
                groups.append(
                    _ConsolidationGroup(
                        merged_id=sorted_ids[0],
                        source_wp_ids=sorted_ids,
                        merge_order=sorted_ids,
                        name=None,
                        goal=None,
                    )
                )
    return groups


def consolidate_wps(
    refined_wps_path: str,
    planner_dir: str,
    **kwargs: object,
) -> dict[str, str]:
    """Merge trivial WPs according to consolidation manifests.

    Reads manifests from ``{planner_dir}/work_packages/consolidation/``.
    Writes ``{planner_dir}/consolidated_wps.json`` and rebuilds
    ``{planner_dir}/wp_index.json``. Returns a result dict with string values.
    """
    try:
        wps_doc: dict[str, Any] = json.loads(Path(refined_wps_path).read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"failed to load refined WPs from {refined_wps_path}: {exc}") from exc
    work_packages: list[dict[str, Any]] = wps_doc.get("work_packages", [])
    task = wps_doc.get("task", "")
    source_dir = wps_doc.get("source_dir", "")

    missing_id = [i for i, wp in enumerate(work_packages) if "id" not in wp]
    if missing_id:
        raise ValueError(f"work_packages entries at indices {missing_id} are missing 'id' field")
    wp_by_id: dict[str, dict[str, Any]] = {wp["id"]: wp for wp in work_packages}

    consolidation_dir = Path(planner_dir) / "work_packages" / "consolidation"
    manifests = _load_manifests(consolidation_dir) if consolidation_dir.exists() else []

    source_to_merged, merged_groups = _build_group_maps(manifests)

    # Validate all IDs in each group exist in wp_by_id
    for group in merged_groups.values():
        if group.merged_id not in wp_by_id:
            raise ValueError(f"unknown merged_id: {group.merged_id}")
        for src_id in group.source_wp_ids:
            if src_id not in wp_by_id:
                raise ValueError(f"unknown WP: {src_id}")
        for ord_id in group.merge_order:
            if ord_id not in wp_by_id:
                raise ValueError(f"unknown merge_order element: {ord_id}")

    has_real_merges = any(len(g.source_wp_ids) > 1 for g in merged_groups.values())
    if not has_real_merges:
        fallback_groups = _build_fallback_groups(work_packages)
        for fg in fallback_groups:
            merged_groups[fg.merged_id] = fg
            for src_id in fg.source_wp_ids:
                source_to_merged[src_id] = fg.merged_id

    # Build the output WP list
    output_wps: list[dict[str, Any]] = []
    non_primary_sources: set[str] = set()
    for group in merged_groups.values():
        for src_id in group.source_wp_ids:
            if src_id != group.merged_id:
                non_primary_sources.add(src_id)

    groups_applied = 0
    for wp in work_packages:
        wp_id = wp["id"]
        if wp_id in non_primary_sources:
            # Absorbed into a merged WP
            continue
        if wp_id in merged_groups:
            group = merged_groups[wp_id]
            if len(group.source_wp_ids) > 1:
                groups_applied += 1
            merged_wp = _merge_group(group, wp_by_id)
            validate_wp_result(merged_wp)
            output_wps.append(merged_wp)
        else:
            output_wps.append(dict(wp))

    # Rewrite depends_on for every output WP
    for wp in output_wps:
        wp_id = wp["id"]
        own_group_sources: set[str] = set()
        if wp_id in merged_groups:
            own_group_sources = set(merged_groups[wp_id].source_wp_ids)
        wp["depends_on"] = _rewrite_deps(wp, source_to_merged, own_group_sources)

    planner_path = Path(planner_dir)
    consolidated_path = planner_path / "consolidated_wps.json"
    write_versioned_json(
        consolidated_path,
        {"task": task, "source_dir": source_dir, "work_packages": output_wps},
        1,
    )

    # Rebuild wp_index.json as a sorted list (ListComp arg keeps AST scan from flagging as dict)
    wp_index_path = planner_path / "work_packages" / "wp_index.json"
    atomic_write(
        wp_index_path,
        json.dumps(
            [
                {"id": wp["id"], "name": wp["name"], "summary": wp.get("summary", "")}
                for wp in sorted(output_wps, key=lambda w: _natural_sort_key(w["id"]))
            ]
        ),
    )

    wp_dir = planner_path / "work_packages"
    if wp_dir.exists():
        for wp in output_wps:
            atomic_write(wp_dir / f"{wp['id']}_result.json", json.dumps(wp))
        for absorbed_id in non_primary_sources:
            result_file = wp_dir / f"{absorbed_id}_result.json"
            if result_file.exists():
                result_file.unlink()

    return {
        "consolidated_wps_path": str(consolidated_path),
        "total_count": str(len(output_wps)),
        "merged_count": str(groups_applied),
    }
