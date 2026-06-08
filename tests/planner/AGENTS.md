# planner/

Planner manifest, validation, compilation, and merge tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Planner test helpers: factories, JSON writer, output dir builder |
| `test_compiler.py` | Tests for compile_plan callable |
| `test_consolidation.py` | Tests for consolidate_wps |
| `test_consolidation_merge.py` | Consolidation core merge behavior and fallback heuristic |
| `test_consolidation_writeback.py` | Consolidation write-back, pipeline integration, lifecycle registry |
| `test_consolidation_cycles.py` | Consolidation cycle-breaking via greedy FAS |
| `test_dag_ops.py` | Tests for _dag_ops: topological_sort, find_sccs, break_cycles_greedy_fas, filter_self_references |
| `test_dag_ops_lazy_import.py` | Regression guard: verifies networkx is not eagerly imported at module level in _dag_ops |
| `test_elaborate_assignments_contract.py` | Contract conformance tests for planner-elaborate-assignments skill |
| `test_lifecycle.py` | LifecycleCategory enum, record_lifecycle_event multi-write preservation, archived_stubs exemption tests |
| `test_elaborate_wps_contract.py` | Contract conformance tests for planner-elaborate-wps skill |
| `test_manifests_builders.py` | build_phase_assignment_manifest and build_phase_wp_manifest |
| `test_manifests_finalize.py` | finalize_wp_manifest and collect_tier_result_files |
| `test_manifests_expansion.py` | expand_assignments, expand_wps, resolve_task_input |
| `test_merge_files.py` | merge_files and merge_tier_results (core merge operations) |
| `test_merge_items.py` | extract_item and replace_item (item-level CRUD) |
| `test_plan_metadata.py` | Tests for plan_id and source_commit stamping in plan.json, manifest.json, plan.md, and issue .md files |
| `test_merge_snapshot.py` | build_plan_snapshot |
| `test_merge_refine.py` | Refine contexts and merge_refined_assignments |
| `test_pipeline_integration.py` | End-to-end pipeline integration tests |
| `test_planner_api.py` | Package scaffold, create_run_dir, feature registry, import smoke |
| `test_planner_validation_gate.py` | ARCH-010 validation gate: validate_refined_*, resolve_wp_id |
| `test_planner_contracts.py` | Atomic-write guards, task context propagation, ID contract enforcement |
| `test_refine_assignments_contract.py` | Contract conformance tests for planner-refine-assignments skill |
| `test_refine_phases_contract.py` | Contract conformance tests for planner-refine-phases skill |
| `test_refine_wps_contract.py` | Contract conformance tests for planner-refine-wps skill |
| `test_schema_conformance.py` | Schema conformance: SKILL.md-compliant data flows through pipeline |
| `test_sort_utils.py` | Tests for _natural_sort_key |
| `test_typed_dict_conformance.py` | TypedDict conformance: required-key sets, factory validation |
| `test_validation_core.py` | Core validate_plan happy/fail paths and severity model |
| `test_validation_checks.py` | Individual _check_* function unit tests |
| `test_validation_discovery.py` | discover_tier_files, _load_* tier loaders, DAG decomposition, lifecycle registry |

## Architecture Notes

`conftest.py` provides shared planner test helpers. The `fixtures/` subdirectory contains YAML data files used by planner tests.
