# infra/

CI/CD configuration, security, guard coverage, and release sanity tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_pretty_output_helpers.py` | Shared helpers for pretty_output hook tests |
| `_token_summary_helpers.py` | Shared helpers for token_summary_appender hook tests |
| `conftest.py` | FormatterCoverageDef NamedTuple and _FORMATTER_COVERAGE_REGISTRY — maps all 11 _FORMATTERS dispatch keys to TypedDict + frozenset pairs for meta-test enforcement |
| `test_anyio_infra.py` | REQ-DEP-001 through REQ-DEP-004: anyio declared as direct dependency |
| `test_ask_user_question_guard.py` | Tests for the ask_user_question_guard PreToolUse hook |
| `test_branch_protection_guard.py` | Tests for hooks/branch_protection_guard.py — PreToolUse branch protection |
| `test_ci_dev_config.py` | Structural enforcement: CI workflow and pre-commit configuration must contain required quality gates |
| `test_ci_workflow.py` | CI workflow structural tests |
| `test_claude_md_critical_rules.py` | Tests that CLAUDE.md contains required critical rules (FRICT-1B-3, FRICT-3A-1) |
| `test_command_guard_completeness.py` | Structural meta-test: command-inspecting guards must cover all command-executing tools |
| `test_coverage_audit.py` | Tests for scripts/compare-coverage-ast.py — AST extraction and coverage comparison |
| `test_dependency_pins.py` | Dependency pin guards (REQ-DEP-001, REQ-DEP-002) — pytest 9.x, igraph bounds |
| `test_docstring_labels.py` | Tests for correct docstring layer labels across the codebase |
| `test_filter_activation.py` | Infrastructure tests: verify test path filtering is activated in project config |
| `test_fleet_dispatch_guard.py` | Tests for fleet_dispatch_guard.py PreToolUse hook |
| `test_generated_file_write_guard.py` | Tests for generated_file_write_guard.py PreToolUse hook |
| `test_generated_files.py` | Tests that generated files with machine-local paths are not tracked in git |
| `test_gitattributes.py` | REQ-R741-A03: .gitattributes must exist and mark vendored JS as binary |
| `test_grep_pattern_lint_guard.py` | Tests for grep_pattern_lint_guard.py — PreToolUse hook for Grep tool pattern syntax |
| `test_guard_coverage.py` | Structural test: destructive tools have PreToolUse hook coverage |
| `test_skill_orchestration_guard.py` | Tests for skill_orchestration_guard.py PreToolUse hook |
| `test_manifest_completeness.py` | Manifest completeness and orphan detection tests for the test-filter manifest |
| `test_mcp_health_guard.py` | Tests for mcp_health_guard PreToolUse hook |
| `test_open_kitchen_guard.py` | Phase 2 tests: open_kitchen_guard PreToolUse hook |
| `test_planner_gh_discovery_guard.py` | Tests for the planner_gh_discovery_guard PreToolUse hook |
| `test_pr_create_guard.py` | Tests for the pr_create_guard PreToolUse hook |
| `test_pretty_output_formatters.py` | Tests: pretty_output per-tool named formatters |
| `test_pretty_output_generic_and_wrap.py` | Tests: pretty_output generic formatter and Claude Code double-wrap |
| `test_pretty_output_hook_infra.py` | Tests: pretty_output hook infrastructure, fail-open, and coverage contracts |
| `test_pretty_output_integration.py` | End-to-end schema consistency tests for the pretty_output hook |
| `test_pretty_output_recipe.py` | Tests: pretty_output token/timing, load_recipe, list_recipes, open_kitchen, deduplication |
| `test_pyproject_bounds.py` | Tests for pyproject.toml version lower bounds |
| `test_pyproject_metadata.py` | Verify pyproject.toml contains required public release metadata |
| `test_resume_ownership_guard.py` | Tests for resume_ownership_guard.py PreToolUse hook — ownership validation at resume |
| `test_release_sanity.py` | Release-readiness sanity checks |
| `test_release_workflows.py` | Structural contract tests for the release CI workflows |
| `test_remove_clone_guard.py` | Tests for the remove_clone_guard PreToolUse hook |
| `test_schema_version_convention.py` | Allowlist ratchet: enforce that new JSON dict write sites use write_versioned_json |
| `test_security_config.py` | Structural tests for security configuration integrity |
| `test_session_scope_enforcement.py` | Structural enforcement tests: session-scope metadata on HookDef |
| `test_skill_cmd_check.py` | Unit tests for the skill_cmd_check PreToolUse hook |
| `test_skill_load_guard.py` | Tests for guards/skill_load_guard.py PreToolUse hook — denies native tools until Skill called |
| `test_skill_command_guard.py` | Tests for the skill_command_guard PreToolUse hook |
| `test_taskfile.py` | Taskfile structural tests |
| `test_testmon_eval.py` | Testmon eval tests |
| `test_token_summary_core.py` | Tests: token_summary_appender core — existence, early-exit, happy path, session filtering |
| `test_token_summary_filters.py` | Tests: token_summary_appender unit helpers and order_id isolation |
| `test_unsafe_install_guard.py` | Tests for the unsafe_install_guard PreToolUse hook |

## Architecture Notes

`_pretty_output_helpers.py` and `_token_summary_helpers.py` provide shared helper factories used across the split pretty_output and token_summary test files respectively.
