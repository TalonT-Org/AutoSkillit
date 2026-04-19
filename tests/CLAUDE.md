# Test Development Guidelines

## xdist Compatibility

All tests run under `-n 4` (xdist default: `--dist load`). Every test must be safe for parallel execution:
- Use `tmp_path` for filesystem isolation — never write to shared locations
- Session-scoped fixtures run once per worker process, not once globally
- Module-level globals are per-worker (separate processes) — no cross-worker state sharing
- Use `monkeypatch.setattr()` for all module-level state mutations — never bare assignment
- Source directories passed to `clone_repo` must be **subdirectories** of `tmp_path`,
  not `tmp_path` itself. When `source_dir = tmp_path`, `clone_repo` places
  `autoskillit-runs/` at `tmp_path.parent` (worker-shared). Use `source_dir = tmp_path / "repo"`.

## Fixture Discipline

- The `tool_ctx` fixture (conftest.py) provides a fully isolated `ToolContext` via
  `make_context()` — a full-stack L3 fixture that imports all production layers. Use for
  server integration tests that need executor, tester, recipes, or other service fields.
  It monkeypatches `server._ctx` so all server tool handler calls use the test context
  without global state leakage.
- The `minimal_ctx` fixture (conftest.py) provides a lightweight `ToolContext` using only
  L0+L1 imports (core, pipeline, config). Use for tests that only need gate, audit,
  token_log, timing_log, or config — no server factory, no L2/L3 service wiring. Does NOT
  monkeypatch `server._state._ctx`. Guard tests in `test_conftest.py` enforce the import
  boundary via AST analysis.
- To test with the kitchen closed, set `ctx.gate = DefaultGateState(enabled=False)` at
  the start of the test or in a class-level autouse fixture (see `_close_kitchen` in
  `test_instruction_surface.py` for an example).
- Never use bare assignment or `try/finally` to restore server state — use `monkeypatch` or
  rely on the fixture's teardown.

## Layer Markers

Every `test_*.py` file in a source-layer-mirroring directory carries a module-level
`pytestmark` with a `layer` marker matching the directory name:

```python
pytestmark = [pytest.mark.layer("execution")]
```

**In-scope directories:** core, config, pipeline, execution, workspace, recipe,
migration, server, cli.

**Out of scope:** arch/, contracts/, infra/, docs/, skills/, hooks/, skills_extended/.

When a file already defines `pytestmark` for other markers (e.g., `skipif`, `anyio`),
use list form and place the `layer` marker first.

The `layer` marker is registered in `pyproject.toml`. Conftest validates at collection
time that marker values match directories (warnings on mismatch).
`tests/arch/test_layer_markers.py` enforces completeness and correctness via AST scan.

**Usage:** `pytest -m 'layer("core")'` runs only L0 core tests.

## Size Markers

Test files in annotated directories carry a size marker indicating resource constraints:

```python
pytestmark = [pytest.mark.layer("core"), pytest.mark.small]
```

**Size definitions (Google-style):**

| Marker | Constraints | Examples |
|--------|------------|---------|
| `small` | No persistent I/O, no network, no subprocess. RAM-backed tmpfs via `tmp_path` IS allowed. | Pure logic, string parsing, in-memory dataclass tests |
| `medium` | Filesystem and subprocess allowed. No network, no external services. | Tests spawning child processes, real file system operations |
| `large` | Everything allowed. Full integration. Default for unannotated tests. | End-to-end tests, network calls, Claude API access |

**In-scope directories:** core, pipeline (initial rollout). Other directories follow incrementally.

**Aggressive filter behavior:** When `AUTOSKILLIT_TEST_FILTER=aggressive`, only `small` and `medium` tests run. Unannotated tests default to `large` and are deselected.

**Rules:**
- Each file has exactly one size marker — no conflicts (enforced by `tests/arch/test_size_markers.py`)
- Place size marker after the `layer` marker in the `pytestmark` list
- When in doubt, use `medium` — it's safer to over-classify than under-classify
- `tests/arch/test_size_markers.py` enforces completeness via AST scan

**Usage:** `pytest -m small` runs only small tests. `pytest -m 'small or medium'` excludes large tests.

## Placement Convention: tests/skills/ vs tests/contracts/

- `tests/skills/` — tests that exercise the skill loader, skill discovery, or skill
  resolution infrastructure (SkillResolver, SessionSkillManager, etc.)
- `tests/contracts/` — tests that verify SKILL.md contract content: required sections,
  output patterns, schema validity

## Performance

- `PYTHONDONTWRITEBYTECODE=1` is set via Taskfile — no `.pyc` disk writes
- Test temp I/O is routed to platform-resolved paths:
  - **Linux / WSL2**: `/dev/shm/pytest-tmp` (kernel tmpfs, RAM-backed)
  - **macOS**: `/tmp/pytest-tmp` (disk-backed system default)
- `TMPDIR` is set to the platform path via Taskfile — all `tempfile` calls are routed there
- `--basetemp` is passed to pytest — `tmp_path` fixtures resolve to the platform path
- `cache_dir` is redirected to the platform cache path — no stray pytest cache writes
- `test_tmp_path_is_ram_backed` in `tests/arch/test_ast_rules.py` enforces the `/dev/shm` prefix
  on Linux; on macOS it is a no-op (disk temp is acceptable there)

## Path Filtering

Tests support opt-in path-based filtering to run only the test directories affected by
changed files. Controlled by env var + CLI flags:

- **Opt-in**: Set `AUTOSKILLIT_TEST_FILTER=1` (or `=conservative` / `=aggressive`)
- **CLI override**: `--filter-mode=conservative|aggressive|none`
- **Base ref override**: `--filter-base-ref=<branch>` (default: reads `AUTOSKILLIT_TEST_BASE_REF` then `GITHUB_BASE_REF`)

**Filter algorithm** (`tests/_test_filter.py`):

1. **Fail-open gate**: If env var is unset/falsy, all tests run. On any error, all tests run.
2. **Changed files**: `git merge-base HEAD base_ref` → SHA, then `git diff --name-only <sha>` (working tree vs merge-base: committed + staged + unstaged tracked) + `git ls-files --others --exclude-standard` (new untracked files). Union of all three — a strict superset of the old three-dot form. **Known limitation**: `git rm --cached` (stage-only deletions) are not captured — the file still exists on disk so the working-tree diff misses the deletion. This is acceptable given the fail-open design.
3. **Bucket A**: If any "global impact" file changed (conftest.py, pyproject.toml, etc.) -> full run
4. **Large changeset**: >30 files -> full run
5. **Classification**: src Python -> layer cascade, test Python -> direct, non-Python -> manifest lookup
6. **Always-run**: `arch/` + `contracts/` always included (+ `infra/` + `docs/` in conservative mode)
7. **Deselection**: `pytest_collection_modifyitems` deselects items outside scope paths

**Modes**:

| Mode | Cascade | Always-run | Use case |
|------|---------|-----------|----------|
| `conservative` | Wide (L0 core -> all layers) | arch, contracts, infra, docs | CI, merge gates |
| `aggressive` | Narrow (each package -> itself) | arch, contracts | Local dev |
| `none` | N/A | N/A | Full run (default) |

## Coverage Audit

A quarterly coverage audit validates that the test suite covers all production functions
and that the test filter cascade maps are not hiding blind spots.

**Schedule:** Run `task coverage-audit` quarterly (January, April, July, October) or
after significant architectural changes (new subpackages, major refactors).

**Workflow:**
1. `task coverage-audit` runs the full test suite with `--cov-context=test --cov-branch`
2. `scripts/compare-coverage-ast.py` queries the `.coverage` SQLite database
3. AST-derived function map is compared against actual coverage
4. Report identifies uncovered and partially covered functions
5. Results saved to `temp/coverage-audit-{timestamp}.json`

**Interpreting results:**
- **Uncovered functions**: Production code with zero test coverage — potential blind spots
  in the test filter cascade maps
- **Partially covered functions**: Functions where some branches are untested
- Exit code is always 0 (audit tool, not a gate)

```
tests/
├── CLAUDE.md                            # xdist compatibility guidelines
├── __init__.py
├── _helpers.py
├── conftest.py                          # Shared fixtures: minimal_ctx, tool_ctx, _make_result, _make_timeout_result
├── fakes.py                             # Protocol-based test fakes: InMemory*, MockSubprocessRunner
├── test_conftest.py                     # Tests for conftest fixtures
├── test_phase2_skills.py
├── test_skill_preambles.py
├── test_version.py                      # Version health tests
├── arch/                                # AST enforcement + sub-package layer contracts
│   ├── __init__.py
│   ├── _helpers.py                      # Shared AST visitor infrastructure
│   ├── _rules.py                        # Reusable AST rule definitions
│   ├── test_anyio_migration.py          # Anyio migration guards
│   ├── test_ast_rules.py
│   ├── test_cli_decomposition.py
│   ├── test_gfm_rendering_guard.py
│   ├── test_never_raises_contracts.py
│   ├── test_import_paths.py
│   ├── test_layer_enforcement.py
│   ├── test_registry.py
│   └── test_subpackage_isolation.py
├── cli/                                 # CLI command tests
│   ├── __init__.py
│   ├── test_ansi.py
│   ├── test_cook_interactive.py
│   ├── test_cli_hooks.py
│   ├── test_cli_marketplace.py
│   ├── test_cli_prompts.py
│   ├── test_cli_serve_logging.py
│   ├── test_cook.py
│   ├── test_doctor.py
│   ├── test_init.py
│   ├── test_install.py
│   ├── test_input_tty_contracts.py
│   ├── test_interactive_subprocess_contracts.py
│   ├── test_mcp_names.py
│   ├── test_onboarding.py
│   ├── test_stale_check.py
│   ├── test_subprocess_env_contracts.py
│   ├── test_terminal.py
│   └── test_workspace.py
├── config/                              # Config loading tests
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_helpers.py                  # resolve_ingredient_defaults (moved from server/ in groupG)
│   ├── test_settings_staged_label.py
│   └── test_settings_allowed_labels.py
├── contracts/                           # Protocol satisfaction + package gateway contracts
│   ├── __init__.py
│   ├── test_claim_issue_contracts.py
│   ├── test_claude_code_interface_contracts.py
│   ├── test_collapse_issues_contracts.py
│   ├── test_github_ops.py
│   ├── test_instruction_surface.py
│   ├── test_issue_content_fidelity.py
│   ├── test_issue_splitter_contracts.py
│   ├── test_l1_packages.py
│   ├── test_open_pr_contracts.py
│   ├── test_package_gateways.py
│   ├── test_pr_traceability_contracts.py
│   ├── test_prepare_issue_contracts.py
│   ├── test_process_issues_contracts.py
│   ├── test_protocol_satisfaction.py
│   ├── test_skill_contracts.py
│   ├── test_target_skill_invocability.py
│   ├── test_triage_contracts.py
│   ├── test_api_surface_alignment.py
│   ├── test_sous_chef_routing.py
│   ├── test_sous_chef_scheduling.py
│   ├── test_tools_recipe_contracts.py
│   ├── test_review_pr_diff_annotation.py
│   └── test_version_consistency.py
├── core/                                # Core layer tests
│   ├── __init__.py
│   ├── test_add_dir_validation.py
│   ├── test_branch_guard.py             # (moved from pipeline/ in groupG)
│   ├── test_core.py
│   ├── test_github_url.py
│   ├── test_io.py
│   ├── test_logging.py
│   ├── test_paths.py
│   ├── test_types.py
│   ├── test_core_terminal_table.py
│   ├── test_type_constants.py
│   └── test_types_structure.py
├── docs/                                # Documentation integrity tests
│   ├── __init__.py
│   ├── test_banned_phrases.py           # Prohibited phrases not present in documentation
│   ├── test_doc_counts.py               # Counts of tools, skills, hooks, recipes (regression guard)
│   ├── test_doc_index.py                # Doc file index integrity
│   ├── test_doc_links.py                # Internal and external link validity
│   ├── test_filename_naming.py          # Documentation filename conventions
│   └── test_glossary_spelling.py        # Glossary term spelling consistency
├── execution/                           # Subprocess integration + session tests
│   ├── __init__.py
│   ├── test_anomaly_detection.py
│   ├── test_ci.py
│   ├── test_ci_params.py
│   ├── test_commands.py
│   ├── test_db.py
│   ├── test_diff_annotator.py
│   ├── test_flag_contracts.py
│   ├── test_github.py
│   ├── test_headless.py
│   ├── test_headless_add_dirs.py
│   ├── test_headless_debug_logging.py
│   ├── test_headless_env_injection.py   # (moved from root test_phase2_headless_env.py in groupG)
│   ├── test_linux_tracing.py
│   ├── test_llm_triage.py
│   ├── test_merge_queue.py
│   ├── test_normalize_subtype.py
│   ├── test_output_format_contract.py
│   ├── test_pr_analysis.py
│   ├── test_process_race.py
│   ├── test_process_channel_b.py
│   ├── test_process_debug_logging.py
│   ├── test_process_jsonl.py
│   ├── test_process_kill.py
│   ├── test_process_monitor.py
│   ├── test_process_pty.py
│   ├── test_process_run.py
│   ├── test_process_submodules.py
│   ├── test_quota.py
│   ├── test_remote_resolver.py
│   ├── test_session.py
│   ├── test_session_adjudication.py
│   ├── test_session_debug_logging.py
│   ├── test_session_log.py
│   ├── test_session_log_integration.py
│   ├── test_testing.py
│   └── test_zero_write_detection.py
├── infra/                               # CI/CD and security configuration tests
│   ├── __init__.py
│   ├── test_anyio_infra.py
│   ├── test_branch_protection_guard.py
│   ├── test_ci_dev_config.py
│   ├── test_claude_md_critical_rules.py
│   ├── test_coverage_audit.py
│   ├── test_docstring_labels.py
│   ├── test_generated_files.py
│   ├── test_guard_coverage.py
│   ├── test_leaf_orchestration_guard.py
│   ├── test_hook_executability.py
│   ├── test_hook_registration_coverage.py
│   ├── test_open_kitchen_guard.py       # (moved from root test_phase2_hooks.py in groupG)
│   ├── test_pretty_output.py
│   ├── test_pretty_output_integration.py
│   ├── test_pyproject_bounds.py
│   ├── test_pyproject_metadata.py       # (moved from root in groupG; path constant updated)
│   ├── test_quota_check.py
│   ├── test_release_sanity.py           # (moved from root in groupG; path constant updated)
│   ├── test_release_workflows.py
│   ├── test_remove_clone_guard.py
│   ├── test_security_config.py
│   ├── test_skill_cmd_check.py
│   ├── test_skill_command_guard.py
│   ├── test_taskfile.py
│   ├── test_hook_sync.py
│   ├── test_session_start_reminder.py
│   ├── test_token_summary_appender.py
│   └── test_unsafe_install_guard.py
├── migration/                           # Migration engine and store tests
│   ├── __init__.py
│   ├── test_engine.py
│   ├── test_loader.py
│   ├── test_store.py
│   └── test_api.py
├── pipeline/                            # Audit log, gate, fidelity, and PR-gate tests
│   ├── __init__.py
│   ├── test_audit.py
│   ├── test_context.py
│   ├── test_background_supervisor.py
│   ├── test_fidelity.py                 # (moved from root test_review_pr_fidelity.py in groupG)
│   ├── test_gate.py
│   ├── test_mcp_response.py
│   ├── test_pr_domain_partitioner.py    # (moved from root in groupG)
│   ├── test_pr_gates.py                 # (moved from root test_analyze_prs_gates.py in groupG)
│   ├── test_telemetry_formatter.py
│   ├── test_timings.py
│   └── test_tokens.py
├── recipe/                              # Recipe I/O, validation, schema tests
│   ├── __init__.py
│   ├── conftest.py
│   ├── test__api.py                     # private _api module tests
│   ├── test_anti_pattern_guards.py
│   ├── test_api.py
│   ├── test_bundled_recipe_hidden_policy.py
│   ├── test_bundled_recipes.py
│   ├── test_contracts.py
│   ├── test_diagrams.py
│   ├── test_hidden_ingredients.py
│   ├── test_implementation.py
│   ├── test_implementation_groups.py
│   ├── test_implementation_sprint_mode.py
│   ├── test_io.py
│   ├── test_issue_url_pipeline.py
│   ├── test_loader.py
│   ├── test_merge_prs.py
│   ├── test_merge_prs_queue.py
│   ├── test_merge_sub_recipe_hidden.py
│   ├── test_remediation_recipe.py
│   ├── test_remediation_sprint_mode.py
│   ├── test_rule_decomposition.py
│   ├── test_rules_bypass.py
│   ├── test_rules_ci.py
│   ├── test_rules_clone.py
│   ├── test_rules_contracts.py
│   ├── test_rules_dataflow.py
│   ├── test_rules_inputs.py
│   ├── test_rules_pipeline_internal.py
│   ├── test_rules_project_local_override.py
│   ├── test_rules_recipe.py
│   ├── test_rules_skill_content.py
│   ├── test_rules_skills.py
│   ├── test_rules_structure.py
│   ├── test_rules_subset_disabled.py
│   ├── test_rules_tools.py
│   ├── test_rules_verdict.py
│   ├── test_rules_worktree.py
│   ├── test_schema.py
│   ├── test_skill_emit_consistency.py
│   ├── test_smoke_pipeline.py
│   ├── test_smoke_utils.py
│   ├── test_sprint_sub_recipe.py
│   ├── test_staleness_cache.py
│   ├── test_sub_recipe_loading.py
│   ├── test_sub_recipe_schema.py
│   ├── test_sub_recipe_validation.py
│   └── test_validator.py
├── server/                              # Server unit tests (tool handlers)
│   ├── __init__.py
│   ├── test_factory.py
│   ├── test_editable_guard.py
│   ├── test_perform_merge_editable_guard.py
│   ├── test_tools_label_validation.py
│   ├── test_git.py
│   ├── test_headless_session.py         # (moved from root test_phase2_cook_session.py in groupG)
│   ├── test_mcp_overrides.py            # (moved from recipe/ in groupG)
│   ├── test_server_init.py
│   ├── test_service_wrappers.py
│   ├── test_set_commit_status.py
│   ├── test_state.py
│   ├── test_tool_exception_boundary.py
│   ├── test_tools_ci.py
│   ├── test_tools_clone.py
│   ├── test_tools_execution.py
│   ├── test_tools_git.py
│   ├── test_tools_integrations.py
│   ├── test_tools_integrations_release.py
│   ├── test_tools_kitchen.py
│   ├── test_tools_recipe.py
│   ├── test_tools_run_cmd.py
│   ├── test_tools_run_skill_retry.py
│   ├── test_tools_session_diagnostics.py
│   ├── test_tools_status.py
│   ├── test_tools_status_mcp_response.py
│   ├── test_tools_workspace.py
│   └── test_track_response_size.py
├── skills/                              # Skill contract and compliance tests
│   ├── __init__.py
│   ├── test_analyze_prs_contracts.py
│   ├── test_conflict_resolution_guards.py
│   ├── test_deletion_regression_guards.py
│   ├── test_dry_walkthrough_contracts.py
│   ├── test_merge_pr_ci_gate.py
│   ├── test_open_integration_pr_domain_analysis.py
│   ├── test_open_pr_closing_issue.py
│   ├── test_resolve_review_intent_validation.py
│   ├── test_resolve_review_thread_resolution.py
│   ├── test_review_pr_inline_comment_guards.py
│   ├── test_review_pr_verdict_guards.py
│   ├── test_skill_compliance.py
│   ├── test_skill_genericization.py
│   ├── test_skill_output_compliance.py
│   ├── test_skill_placeholder_contracts.py
│   └── test_validate_audit_contracts.py
└── workspace/                           # Workspace and clone tests
    ├── __init__.py
    ├── conftest.py
    ├── test_cleanup.py
    ├── test_clone.py
    ├── test_clone_ci_contract.py
    ├── test_project_local_overrides.py
    ├── test_session_skills.py           # (moved from root test_phase2_session_skills.py in groupG)
    ├── test_clone_registry.py
    └── test_skills.py

temp/                        # Temporary/working files (gitignored)
```
