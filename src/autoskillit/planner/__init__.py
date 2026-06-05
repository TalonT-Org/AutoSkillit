"""planner/ IL-1 package: progressive resolution planner.

Exports: manifests, merge, validation, compiler."""

from __future__ import annotations

from autoskillit.planner.compiler import compile_plan
from autoskillit.planner.consolidation import consolidate_wps
from autoskillit.planner.lifecycle import load_lifecycle_registry, record_lifecycle_event
from autoskillit.planner.manifests import (
    build_phase_assignment_manifest,
    build_phase_wp_manifest,
    create_run_dir,
    expand_assignments,
    expand_wps,
    finalize_wp_manifest,
    reconcile_wp_files,
    resolve_task_input,
)
from autoskillit.planner.merge import (
    build_plan_snapshot,
    extract_item,
    merge_files,
    merge_refined_assignments,
    merge_refined_wps,
    merge_tier_results,
    replace_item,
)
from autoskillit.planner.schema import (
    ASSIGN_RESULT_FILE_RE,
    ASSIGNMENT_REQUIRED_KEYS,
    DELIVERABLE_BOUNDS,
    PHASE_REQUIRED_KEYS,
    PHASE_RESULT_FILE_RE,
    WP_REQUIRED_KEYS,
    WP_RESULT_FILE_RE,
    AssignmentElaborated,
    AssignmentShort,
    PhaseElaborated,
    PhaseShort,
    PlanDocument,
    PlannerManifest,
    PlannerManifestItem,
    ValidationFinding,
    WPElaborated,
    WPShort,
    parse_planner_id,
    resolve_wp_id,
    validate_refined_assignments,
    validate_refined_plan,
)
from autoskillit.planner.validation import DiscoveryResult, discover_tier_files, validate_plan

__all__ = [
    "ASSIGN_RESULT_FILE_RE",
    "DELIVERABLE_BOUNDS",
    "PHASE_RESULT_FILE_RE",
    "WP_RESULT_FILE_RE",
    "build_phase_assignment_manifest",
    "consolidate_wps",
    "build_phase_wp_manifest",
    "compile_plan",
    "create_run_dir",
    "expand_assignments",
    "expand_wps",
    "finalize_wp_manifest",
    "reconcile_wp_files",
    "resolve_task_input",
    "validate_plan",
    "PlannerManifest",
    "PlannerManifestItem",
    "merge_files",
    "merge_refined_assignments",
    "merge_refined_wps",
    "merge_tier_results",
    "extract_item",
    "replace_item",
    "build_plan_snapshot",
    "PlanDocument",
    "PhaseShort",
    "PhaseElaborated",
    "AssignmentShort",
    "AssignmentElaborated",
    "WPShort",
    "WPElaborated",
    "ASSIGNMENT_REQUIRED_KEYS",
    "PHASE_REQUIRED_KEYS",
    "WP_REQUIRED_KEYS",
    "parse_planner_id",
    "resolve_wp_id",
    "validate_refined_assignments",
    "validate_refined_plan",
    "ValidationFinding",
    "discover_tier_files",
    "DiscoveryResult",
    "load_lifecycle_registry",
    "record_lifecycle_event",
]
