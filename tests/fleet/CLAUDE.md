# fleet/

Fleet campaign dispatch, state persistence, and sidecar tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_helpers.py` | Shared helpers for tests/fleet/ test modules |
| `conftest.py` | Shared fixtures for tests/fleet/ |
| `test_api.py` | Tests for fleet._api module (Group J) |
| `test_campaign_capture.py` | Tests for campaign capture extraction and ingredient interpolation (Group J) |
| `test_dispatch_failure_semantics.py` | Group F: Timeout + No-Result-Block failure semantics for fleet dispatch |
| `test_dispatch_lifespan.py` | Group G (fleet part): lifespan_started surface + envelope propagation |
| `test_dispatch_outcome_classifier.py` | Tests for classify_dispatch_outcome() pure classification function |
| `test_error_envelope.py` | Tests for fleet error envelope registry and constructor (Group R) |
| `test_findings_rpc.py` | Tests for autoskillit.fleet._findings_rpc (T15–T21) |
| `test_fleet.py` | Tests for fleet package |
| `test_fleet_e2e.py` | Fleet Group O: end-to-end test suite for fleet dispatch loop |
| `test_fleet_rename_integrity.py` | Fleet rename integrity guard |
| `test_fleet_semaphore.py` | Unit tests for FleetSemaphore (FleetLock semaphore implementation) |
| `test_food_truck_prompt.py` | Tests for fleet/_prompts.py: _build_food_truck_prompt behavioral semantics |
| `test_gate_state_persistence.py` | Tests for gate dispatch state persistence and campaign state writes |
| `test_helpers_exports.py` | Tests that shared helpers are importable from tests.fleet._helpers |
| `test_liveness.py` | Liveness tests for Linux proc helpers |
| `test_pack_enforcement.py` | Fleet per-recipe tool-surface enforcement tests |
| `test_pack_enforcement_e2e.py` | Fleet per-recipe tool-surface e2e tests using a real MCP server subprocess |
| `test_result_parser.py` | Tests for fleet.result_parser — L2 result block parsing |
| `test_sidecar.py` | Sidecar tests |
| `test_state.py` | Tests for fleet state module (Group J) |
| `test_state_protection.py` | Tests for fleet.state.build_protected_campaign_ids (PROT_1–PROT_9) |
| `test_state_schema.py` | Tests for DispatchRecord schema v2 fields and backward compatibility (Group J) |
| `test_summary.py` | Tests for fleet campaign summary schema v1 (Group S) |

## Architecture Notes

`conftest.py` and `_helpers.py` provide shared fixtures and helper factories for fleet tests. `test_helpers_exports.py` guards that `_helpers` is importable from other test modules.
