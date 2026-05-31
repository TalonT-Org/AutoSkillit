# fleet/

Fleet campaign dispatch, state persistence, and sidecar tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_helpers.py` | Shared helpers for tests/fleet/ test modules |
| `conftest.py` | Shared fixtures for tests/fleet/ |
| `test_dispatch_reaper.py` | Tests for `fleet._dispatch_reaper.reap_stale_dispatches` — orphan kill, dead pid, recycled pid, create_time fallback, dry-run, idempotency |
| `test_api.py` | Tests for fleet._api module (Group J) |
| `test_api_split_integrity.py` | Structural guard: fleet `_api.py` split — verifies new modules export expected symbols and public API surface is preserved |
| `test_api_dispatch_marker.py` | Tests for _run_dispatch marker lifecycle via execution_marker context manager |
| `test_campaign_capture.py` | Tests for campaign capture extraction and ingredient interpolation (Group J) |
| `test_capture_roundtrip.py` | Tests for prompt-extractor field name alignment — verifies sentinel examples use bare names matching `_extract_captures` expectations |
| `test_checkpoint_bridge.py` | Tests for checkpoint_from_sidecar converting IssueSidecarEntry to SessionCheckpoint |
| `test_dispatch_failure_semantics.py` | Group F: Core failure path semantics — timeout, no-sentinel, completed-dirty, completed-clean |
| `test_dispatch_envelope_fields.py` | Dispatch envelope field persistence — elapsed_seconds and dispatch_status |
| `test_dispatch_stderr_forwarding.py` | Stderr envelope forwarding and truncation tests |
| `test_dispatch_ingredient_validation.py` | Missing required ingredient validation gate |
| `test_dispatch_recipe_kind_gate.py` | Recipe kind dispatch gate — FOOD_TRUCK accepted, CAMPAIGN rejected |
| `test_dispatch_crash_diagnostics.py` | Crash path diagnostic persistence and structured logging |
| `test_dispatch_labels_cleaned.py` | Labels_cleaned field persistence on failure/success outcomes |
| `test_dispatch_identity_continuity.py` | Tests for dispatch_id identity continuity on resume — prior_dispatch_id threading through API layer |
| `test_dispatch_state_handle.py` | Tests for DispatchStateHandle factory and dispatch state invariants — resume path state file creation and capture persistence (Group J) |
| `test_dispatch_lifespan.py` | Group G (fleet part): lifespan_started surface + envelope propagation |
| `test_dispatch_outcome_classifier.py` | Tests for classify_dispatch_outcome() pure classification function |
| `test_dispatch_outcome_classifier_timeout.py` | Timeout inputs for `classify_dispatch_outcome` — RESUMABLE vs FAILURE rules |
| `test_error_code_categorization.py` | `ErrorCodeCategory` exhaustiveness guard and infrastructure failure tests |
| `test_dispatch_classification_integrity.py` | AST test: `DispatchRecord(status=...)` must route through classifier |
| `test_resume_checkpoint_field.py` | `DispatchRecord.resume_checkpoint` and `ResumeDecision.checkpoint` field tests |
| `test_resume_max_attempts.py` | `MAX_CONSECUTIVE_RESUME_ATTEMPTS` guard in `resume_campaign_from_state` |
| `test_resume_preflight.py` | Pre-flight JSONL validation and session chain continuity for fleet resume |
| `test_error_envelope.py` | Tests for fleet error envelope registry and constructor (Group R) |
| `test_findings_rpc.py` | Tests for autoskillit.fleet._findings_rpc (T15–T21) |
| `test_fleet.py` | Tests for fleet package |
| `test_fleet_e2e.py` | Fleet Group O: end-to-end test suite for fleet dispatch loop |
| `test_fleet_rename_integrity.py` | Fleet rename integrity guard |
| `test_fleet_semaphore.py` | Unit tests for FleetSemaphore (FleetLock semaphore implementation) |
| `test_food_truck_prompt.py` | Tests for fleet/_prompts.py: _build_food_truck_prompt behavioral semantics |
| `test_gate_state_persistence.py` | Tests for gate dispatch state persistence and campaign state writes |
| `test_helpers_exports.py` | Tests that shared helpers are importable from tests.fleet._helpers |
| `test_label_cleanup.py` | Tests for fleet._label_cleanup — finally block cleanup on CancelledError, RuntimeError, no-sidecar, no-client, multiple issues; direct unit tests for cleanup_orphaned_labels |
| `test_liveness.py` | Liveness tests for Linux proc helpers |
| `test_pack_enforcement.py` | Fleet per-recipe tool-surface enforcement tests |
| `test_pack_enforcement_e2e.py` | Fleet per-recipe tool-surface e2e tests using a real MCP server subprocess |
| `test_research_campaign_dispatch.py` | Tests for multi-recipe research campaign dispatch capture propagation (Group J) |
| `test_result_parser.py` | Tests for fleet.result_parser — L2 result block parsing |
| `test_retry_failed_dispatch.py` | Tests for explicit retry of failed campaign dispatches via FAILURE → PENDING state transition |
| `test_skip_when.py` | Unit tests for `evaluate_skip_when` — campaign/inputs ref resolution, expression validation, quote stripping |
| `test_sidecar.py` | Sidecar tests |
| `test_startup_label_recovery.py` | Tests for sweep_stale_dispatch_labels — dead dispatch label cleanup, alive dispatch skip, missing sidecar, multi-campaign |
| `test_find_dispatch_for_issue.py` | Tests for find_dispatch_for_issue — running dispatch sidecar lookup, non-running skip, missing sidecar, empty state, corrupt state |
| `test_staleness_propagation.py` | Tests that fleet dispatch proceeds despite process staleness — L2 subprocess revalidates independently |
| `test_state.py` | Tests for fleet state module (Group J) |
| `test_state_lock_contract.py` | Locking contract tests — AST scan for flock targets, flock acquisition per mutation, cross-caller concurrency |
| `test_state_protection.py` | Tests for fleet.state.build_protected_campaign_ids (PROT_1–PROT_9) |
| `test_state_recovery.py` | Tests for derive_orchestrator_resume_spec in state_recovery module |
| `test_state_schema.py` | Tests for DispatchRecord schema v2 fields and backward compatibility (Group J) |
| `test_summary.py` | Tests for fleet campaign summary schema v1 (Group S) |

## Architecture Notes

`conftest.py` and `_helpers.py` provide shared fixtures and helper factories for fleet tests. `test_helpers_exports.py` guards that `_helpers` is importable from other test modules.
