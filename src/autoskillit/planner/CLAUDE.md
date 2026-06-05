# planner/

IL-1 progressive resolution planner — phases, work packages, manifest generation, DAG validation.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `expand_assignments`, `expand_wps`, `finalize_wp_manifest`, `validate_plan`, `compile_plan` |
| `_sort_utils.py` | Natural sort key utility: `_NATURAL_SORT_RE`, `_natural_sort_key()` |
| `_dag_ops.py` | Shared DAG primitives: `topological_sort`, `find_sccs`, `break_cycles_greedy_fas`, `filter_self_references` |
| `manifests.py` | `expand_assignments`, `expand_wps`, `finalize_wp_manifest`, `reconcile_wp_files`, `build_phase_assignment_manifest`, `build_phase_wp_manifest` |
| `merge.py` | `merge_tier_dir`, `merge_files`, `build_plan_snapshot`, `extract_item`, `replace_item` — JSON interchange merge tooling |
| `validation.py` | `validate_plan` — DAG cycle check, structural completeness, sizing bounds, duplicate-deliverable detection |
| `schema.py` | Planner data contracts: `PhaseResult`, `AssignmentResult`, `WPResult`, `PlanDocument` TypedDicts |
| `compiler.py` | `compile_plan` — topological sort, issue body generation, milestone definitions, plan artifacts |
| `consolidation.py` | `consolidate_wps` — post-elaboration WP consolidation: reads manifests, merges trivial WPs, rewrites dep IDs |
| `lifecycle.py` | `record_lifecycle_event`, `load_lifecycle_registry` — unified lifecycle provenance for voided/absorbed entities |

## Architecture Notes
import from `server/` or `recipe/`. `validation.py` performs a DAG cycle check before any
compilation proceeds. `consolidation.py` runs as a post-pass after all elaboration phases
complete.
