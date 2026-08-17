# Real Group 12 + Group 13 manifest text, verbatim from
# `.autoskillit/temp/validate-audit-2026-08-15_203307/grouping_manifest_tests.md:74-87`.
# This reproduces the exact #4610-producing artifact so the live-behavior probe
# can verify that the Step 7 self-check now splits both effort tiers correctly.

### Ticket Group 12: Server Test Splits — High-Effort Pairs
- **Finding IDs**: C9.37-56 (subset: test_tools_kitchen_envelope.py 1369L, test_tools_issue_lifecycle.py 1352L, test_tools_execution_results.py 1097L, test_tools_integrations.py 1014L, test_recipe_section_pagination.py 1326L, test_factory.py 954L)
- **Rationale**: Six HIGH-effort (>900 line) server test files. Per the effort rule, pair with at most one other file. Proposed pairings:
  - **Pair A** (kitchen tool tests): test_tools_kitchen_envelope.py + test_tools_issue_lifecycle.py (both kitchen-domain, both >1300 lines)
  - **Pair B** (execution tool tests): test_tools_execution_results.py + test_tools_integrations.py (both tool-results, both >1000 lines)
  - **Pair C** (cross-cutting): test_recipe_section_pagination.py + test_factory.py (both factory/pagination infrastructure)
- **Scope**: large
- **File overlap**: none between pairs

### Ticket Group 13: Server Test Splits — Medium-Effort Batch
- **Finding IDs**: C9.37-56 (subset: test_pipeline_tracker.py 883L, test_tools_kitchen_visibility.py 872L, test_tools_execution_input_gates.py 810L, test_tools_load_recipe.py 799L, test_tools_ci.py 775L, test_session_type_visibility.py 759L)
- **Rationale**: Six MEDIUM-effort (750-900 line) server test files. Per the effort rule, these can be batched in small groups in the same package. All in `tests/server/`, all have class boundaries for lift-to-file. Single batch ticket.
- **Scope**: medium
- **File overlap**: none
