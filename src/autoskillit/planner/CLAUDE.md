# planner/

IL-1 progressive resolution planner — phases, work packages, manifest generation, DAG validation.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `expand_assignments`, `expand_wps`, `finalize_wp_manifest`, `validate_plan`, `compile_plan` |
| `manifests.py` | `expand_assignments`, `expand_wps`, `finalize_wp_manifest`, `build_phase_assignment_manifest`, `build_phase_wp_manifest` |
| `merge.py` | `merge_tier_dir`, `merge_files`, `build_plan_snapshot`, `extract_item`, `replace_item` — JSON interchange merge tooling |
| `validation.py` | `validate_plan` — DAG cycle check, structural completeness, sizing bounds, duplicate-deliverable detection |
| `schema.py` | Planner data contracts: `PhaseResult`, `AssignmentResult`, `WPResult`, `PlanDocument` TypedDicts |
| `compiler.py` | `compile_plan` — topological sort, issue body generation, milestone definitions, plan artifacts |
| `consolidation.py` | `consolidate_wps` — post-elaboration WP consolidation: reads manifests, merges trivial WPs, rewrites dep IDs |

## Architecture Notes

The planner operates on JSON manifest files written to `.autoskillit/temp/` and does not
import from `server/` or `recipe/`. `validation.py` performs a DAG cycle check before any
compilation proceeds. `consolidation.py` runs as a post-pass after all elaboration phases
complete.
