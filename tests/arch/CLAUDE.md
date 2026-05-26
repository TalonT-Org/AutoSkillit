# arch/

AST enforcement, sub-package layer contracts, and architectural invariant tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_deselection.py` | Diff-aware parametrized deselection helpers |
| `_helpers.py` | Shared AST visitor infrastructure and import analysis utilities |
| `_rules.py` | Canonical source for shared arch-test types, exempt sets, and RULES tuple |
| `conftest.py` | Arch conftest — shared fixtures for AST-based tests |
| `test_anyio_migration.py` | Regression guards for the asyncio→anyio migration (C-6) |
| `test_arch_deselection.py` | Tests for diff-aware parametrized deselection — REQ-ARCH-004 |
| `test_ast_rules.py` | Architectural enforcement: AST-based visitor rules (ARCH-001 through ARCH-009) |
| `test_backend_coherence.py` | Architectural tests for backend coherence enforcement |
| `test_backend_command_path.py` | AST tests enforcing ctx.backend usage for command construction in headless path |
| `test_backend_protocol_completeness.py` | Protocol completeness tests for CodingAgentBackend command builders |
| `test_audit_feature_gates_skill.py` | Structural integrity tests for the audit-feature-gates skill |
| `test_eval_agent_skill.py` | Structural integrity tests for the eval-agent skill |
| `test_boot_step_symmetry.py` | AST guard: both boot functions (_fleet_auto_gate_boot, _food_truck_auto_gate_boot) must call sweep_stale_dispatch_labels |
| `test_bundled_recipes_split.py` | Enforcement: test_bundled_recipes.py split structure guard |
| `test_cascade_map_guard.py` | REQ-GUARD-001..003, 005: CI guard validating cascade maps against AST-derived reverse import graph |
| `test_channel_b_timeout_guard.py` | AST guard: Channel B tests must use timeout >= TimeoutTier.CHANNEL_B |
| `test_dataclass_slots.py` | Architectural invariant: every @dataclass(frozen=True) must also have slots=True |
| `test_cli_decomposition.py` | AST-level tests enforcing CLI decomposition and hook security hardening |
| `test_dispatch_timeout_guard.py` | AST guard: _run_dispatch must call resolve_dispatch_timeout with no hardcoded 'or 1800' or falsy timeout patterns |
| `test_doctor_readonly.py` | AST guard: run_doctor() must not perform filesystem mutations (REQ-DOCTOR-READONLY) |
| `test_execution_source_split.py` | Arch guards for execution layer source splits (P8-F1, F3, F4 audit fixes) |
| `test_feature_markers.py` | Marker completeness: fleet test files carry feature('fleet'), infra tests do not |
| `test_feature_registry.py` | Feature registry structural and behavioral self-tests |
| `test_file_size_budgets.py` | Per-file source size budgets (REQ-FILE-001) |
| `test_filter_contracts.py` | AST-based contract test enforcing signature compatibility between two apply_manifest implementations |
| `test_gfm_rendering_guard.py` | Architectural guard: GFM table rendering must route through _render_gfm_table |
| `test_headless_split.py` | Structural guards for the test_headless.py split (P1-F01 audit fix) |
| `test_skill_result_construction_guard.py` | AST guard: every SkillResult() construction must include kill_reason keyword arg |
| `test_skill_registries.py` | Architectural tests for skill registry completeness |
| `test_clone_guard_policy_enforcement.py` | Enforcement tests for CloneGuardPolicy usage |
| `test_import_layer_labels.py` | Regression guard: no bare L-number labels in import-layer contexts |
| `test_import_linter_contracts.py` | Tests verifying import-linter contract documentation (REQ-ARCH-007) |
| `test_import_paths.py` | Structural import-path compliance tests (REQ-IMP-001, REQ-IMP-002) |
| `test_kitchen_guard_scoping.py` | Architectural enforcement: any_kitchen_open call-site scoping and test helper isolation |
| `test_kitchen_id_assignment.py` | AST guard: ctx.kitchen_id assignment only via resolve_kitchen_id() |
| `test_layer_enforcement.py` | MCP tool registry + import layer contracts + cross-package rules |
| `test_layer_markers.py` | Enforce pytestmark layer markers on all in-scope test files |
| `test_never_raises_contracts.py` | Structural enforcement of 'Never raises' docstring contracts in server/ |
| `test_no_error_dict_return.py` | AST guard: load_and_validate must not return dicts with 'error' key — errors flow via exceptions |
| `test_no_inline_jsonl_request_id_dedup.py` | AST guard: no inline requestId dedup in session_log.py or tool_sequence_analysis.py |
| `test_protocol_names.py` | T5-T6: Protocol naming and DefaultSkillResolver export smoke tests |
| `test_provider_profile_contract.py` | Tier 3 contract: _resolve_provider_profile must never return step_name as profile |
| `test_python_no_hardcoded_temp.py` | Architectural invariant: no literal `.autoskillit/temp` outside the whitelist |
| `test_recipe_rule_registration.py` | REQ-RECIPE-001: every recipe/rules_*.py file must be imported by recipe/__init__.py |
| `test_regex_guards.py` | Arch guard: keyword regexes in cmd-scanning rules must use path-safe lookbehind guards |
| `test_regex_import.py` | Structural guard: src/ must use `import regex as re`, not bare `import re` (hooks/ exempt) |
| `test_registry.py` | Symbolic rule registry tests (RuleDescriptor, RULES, Violation) |
| `test_registry_key_casing.py` | Architectural invariant tests for registry key casing |
| `test_size_markers.py` | Enforce pytestmark size markers on in-scope test files |
| `test_startup_budget.py` | Startup budget enforcement (REQ-STARTUP-001): serve() critical path must not contain subprocess calls |
| `test_subpackage_isolation.py` | IL-1/IL-2/IL-3 sub-package isolation, __all__ completeness, size/file-count constraints |
| `test_subpackage_structure.py` | REQ-ARCH-010: Validate post-reorganization subpackage structure |
| `test_tool_annotation_completeness.py` | AST annotation test shield for MCP tool readOnlyHint semantics (layers 1a, 1b) |
| `test_tools_execution_decomposition.py` | Structural decomposition guard for tools_execution.py split |
| `test_transforms_hygiene.py` | Structural guards for FastMCP visibility tag hygiene |
| `test_watcher_signal_consistency.py` | Structural guard: all process watchers must call `_has_active_dispatch_marker` |
| `test_write_restriction_coverage.py` | Architectural invariant: skills with prose write restrictions in NEVER blocks have runtime enforcement (read_only, output_dir, or allowlist) |

## Architecture Notes

`conftest.py` provides shared fixtures for AST-based tests. `_helpers.py` contains the shared AST visitor infrastructure used across multiple test files. `_rules.py` defines reusable arch rule tuples. `_deselection.py` provides diff-aware parametrized deselection helpers used by tests that use `pytest.mark.parametrize` over large rule sets.
