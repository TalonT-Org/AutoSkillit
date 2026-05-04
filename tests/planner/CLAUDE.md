# planner/

Planner manifest, validation, compilation, and merge tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Planner test helpers |
| `test_compiler.py` | Tests for compile_plan callable |
| `test_consolidation.py` | Tests for autoskillit.planner.consolidation.consolidate_wps |
| `test_elaborate_wps_contract.py` | Contract conformance tests for planner-elaborate-wps skill registration |
| `test_manifests.py` | Planner manifests tests |
| `test_merge.py` | Planner merge tests |
| `test_pipeline_integration.py` | End-to-end pipeline integration tests |
| `test_planner.py` | Tests for the planner L1 subpackage scaffold |
| `test_refine_assignments_contract.py` | Contract conformance tests for planner-refine-assignments skill registration |
| `test_refine_phases_contract.py` | Contract conformance tests for planner-refine-phases skill registration |
| `test_refine_wps_contract.py` | Contract conformance tests for planner-refine-wps skill registration |
| `test_schema_conformance.py` | Schema conformance tests: SKILL.md-compliant data flows correctly through the pipeline |
| `test_typed_dict_conformance.py` | TypedDict conformance: required-key sets, SKILL.md alignment, factory validation |
| `test_validation.py` | Tests for validate_plan callable |

## Architecture Notes

`conftest.py` provides shared planner test helpers. The `fixtures/` subdirectory contains YAML data files used by planner tests.
