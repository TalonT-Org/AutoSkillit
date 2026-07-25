# infra/

CI/CD configuration, security, guard coverage, and release sanity tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_pretty_output_helpers.py` | Shared helpers for pretty_output hook tests |
| `_token_summary_helpers.py` | Shared helpers for token_summary_appender hook tests |
| `conftest.py` | FormatterCoverageDef NamedTuple and _FORMATTER_COVERAGE_REGISTRY — maps all 11 _FORMATTERS dispatch keys to TypedDict + frozenset pairs for meta-test enforcement |
| `test_adr_runtime_guard_coverage.py` | Meta-test: ADR-to-guard mapping completeness — every constrained ADR has a registered runtime guard |
| `test_anyio_infra.py` | REQ-DEP-001 through REQ-DEP-004: anyio declared as direct dependency |
| `test_artifact_download_guard.py` | Tests for the artifact_download_guard PreToolUse hook |
| `test_ask_user_question_guard.py` | Tests for the ask_user_question_guard PreToolUse hook |
| `test_background_exec_guard.py` | Tests for background_exec_guard.py PreToolUse hook — blocks run_in_background=true in skill sessions |
| `test_branch_protection_guard.py` | Tests for hooks/branch_protection_guard.py — PreToolUse branch protection |
| `test_check_pyi_stub_format.py` | Unit tests for scripts/check_pyi_stub_format.py pre-commit hook — validates FunctionDef, ClassDef, and non-relative import rejection |
| `test_check_pyi_stub_symbols.py` | Unit tests for scripts/check_pyi_stub_symbols.py pre-commit hook — validates missing symbol detection, completeness acceptance, underscore skipping, and __all__ usage |
| `test_ci_dev_config.py` | Structural enforcement: CI workflow and pre-commit configuration must contain required quality gates |
| `test_ci_shard_config.py` | Tests for CI shard directory configuration consistency |
| `test_ci_workflow.py` | CI workflow structural tests |
| `test_claude_md_critical_rules.py` | Tests that effective `CLAUDE.md` (resolved via the `@AGENTS.md` include) contains required critical rules from friction analysis; direct ownership of shared rules lives in `AGENTS.md` and is covered by `test_docs_critical_rules.py` and `tests/docs/test_agents_md_content.py` |
| `test_command_guard_completeness.py` | Structural meta-test: command-inspecting guards must cover all command-executing tools |
| `test_command_guard_verb_position.py` | Structural ratchet: command-inspecting guards must not perform raw substring membership against shell command text — guards must tokenize evaluated payloads and compare verb/argument positions |
| `test_conformance_probes_workflow.py` | Structural tests for conformance-probes.yml workflow — triggers, permissions, SHA pinning, installed Codex parse gate, cache gate, post-failure wiring |
| `test_coverage_audit.py` | Tests for scripts/compare-coverage-ast.py — AST extraction and coverage comparison |
| `test_dependency_pins.py` | Dependency pin guards (REQ-DEP-001, REQ-DEP-002) — pytest 9.x, networkx bounds |
| `test_docs_critical_rules.py` | Tests that `AGENTS.md` (and a few physical-`CLAUDE.md` integrity checks) contain required critical rules (FRICT-1B-3, FRICT-3A-1, FRICT-5-2, FRICT-7-1) |
| `test_docstring_labels.py` | Tests for correct docstring layer labels across the codebase |
| `test_fastmcp_version_floor.py` | FastMCP version floor and internal API surface guard |
| `test_filter_activation.py` | Infrastructure tests: verify test path filtering is activated in project config |
| `test_fleet_dispatch_guard.py` | Tests for fleet_dispatch_guard.py PreToolUse hook |
| `test_generated_file_write_guard.py` | Tests for generated_file_write_guard.py PreToolUse hook |
| `test_generated_files.py` | Tests that generated files with machine-local paths are not tracked in git |
| `test_git_ops_guard.py` | Tests for the git_ops_guard PreToolUse hook — blocks destructive git operations in headless sessions |
| `test_gitattributes.py` | REQ-R741-A03: .gitattributes must exist and mark vendored JS as binary |
| `test_grep_pattern_lint_guard.py` | Tests for grep_pattern_lint_guard.py — PreToolUse hook for Grep tool pattern syntax |
| `test_guard_coverage.py` | Structural test: destructive tools have PreToolUse hook coverage |
| `test_manifest_completeness.py` | Manifest completeness and orphan detection tests for the test-filter manifest |
| `test_manifest_directory_completeness.py` | Manifest directory completeness: validates each manifest pattern's test directory list includes all dependent test directories |
| `test_mcp_health_advisor.py` | Tests for mcp_health_advisor PreToolUse hook |
| `test_open_kitchen_guard.py` | Phase 2 tests: open_kitchen_guard PreToolUse hook |
| `test_output_budget_evidence.py` | Fixture-isolated validation of incremental and complete Output Budget Protocol remediation evidence manifests |
| `test_planner_gh_discovery_guard.py` | Tests for the planner_gh_discovery_guard PreToolUse hook |
| `test_plugin_source_ratchets.py` | AST ratchets: no hand-rolled registry resolution, no raw `resolve()` in containment checks, no unprojected `--plugin-dir` |
| `test_pr_create_guard.py` | Tests for the pr_create_guard PreToolUse hook |
| `test_pretty_output_formatters.py` | Tests: pretty_output per-tool named formatters |
| `test_pretty_output_generic_and_wrap.py` | Generic formatter losslessness, artifact-backed reduction, and Claude Code double-wrap tests |
| `test_pretty_output_hook_infra.py` | Formatter infrastructure, spill-metadata trust, recovery-notice, fail-open, and coverage contracts |
| `test_pretty_output_integration.py` | End-to-end schema consistency tests for the pretty_output hook |
| `test_pretty_output_recipe.py` | Recipe formatter, exemption measurement/metadata parity, and deduplication contracts |
| `test_probe_scripts.py` | Tests for CI-facing probe canary shell scripts (post-probe-failure.sh, create-probe-canary-issue.sh) — syntax, executable bit, and env-var validation |
| `test_pyproject_bounds.py` | Tests for pyproject.toml version lower bounds |
| `test_pyproject_metadata.py` | Verify pyproject.toml contains required public release metadata |
| `test_recipe_read_guard.py` | Tests for the recipe_read_guard PreToolUse hook — blocks recipe/skill/agent file reads |
| `test_release_sanity.py` | Release-readiness sanity checks |
| `test_release_workflows.py` | Structural contract tests for the release CI workflows |
| `test_remove_clone_guard.py` | Tests for the remove_clone_guard PreToolUse hook |
| `test_resume_ownership_guard.py` | Tests for resume_ownership_guard.py PreToolUse hook — ownership validation at resume |
| `test_risky_gh_subcommand_coverage.py` | Structural enforcement: risky gh subcommands must have guard coverage |
| `test_risky_git_ops_coverage.py` | Structural enforcement: risky git operations must have guard coverage |
| `test_schema_read_convention.py` | Read-side ratchet: enforce that write_versioned_json callers have corresponding read-side validation |
| `test_schema_version_convention.py` | Allowlist ratchet: enforce that new JSON dict write sites use write_versioned_json |
| `test_security_config.py` | Structural tests for security configuration integrity |
| `test_session_scope_enforcement.py` | Structural enforcement tests: session-scope metadata on HookDef |
| `test_session_type_exemption_enforcement.py` | Structural enforcement tests: session-type exemption metadata on HookDef |
| `test_skill_cmd_check.py` | Unit tests for the skill_cmd_check PreToolUse hook |
| `test_skill_command_guard.py` | Tests for the skill_command_guard PreToolUse hook |
| `test_skill_exemption_enforcement.py` | Structural enforcement tests: skill-exemption metadata on HookDef |
| `test_skill_load_guard.py` | Tests for guards/skill_load_guard.py PreToolUse hook — denies native tools until Skill called |
| `test_skill_orchestration_guard.py` | Tests for skill_orchestration_guard.py PreToolUse hook |
| `test_taskfile.py` | Taskfile structural tests, including installed Codex config-parse and credentialed output-budget smoke gates |
| `test_testmon_eval.py` | Testmon eval tests |
| `test_token_summary_core.py` | Tests: token_summary_appender core — early-exit, happy path, session filtering, efficiency table |
| `test_token_summary_filters.py` | Tests: token_summary_appender unit helpers (_canonical, _humanize, _format_table, _unwrap_mcp_response), order_id isolation, and config key migration |
| `test_token_summary_v1_compat.py` | Tests: token_summary_appender v1 sessions.jsonl and token_usage.json backward compatibility |
| `test_unsafe_install_guard.py` | Tests for the unsafe_install_guard PreToolUse hook |

## Architecture Notes

`_pretty_output_helpers.py` and `_token_summary_helpers.py` provide shared helper factories used across the split pretty_output and token_summary test files respectively.
