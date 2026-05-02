# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides MCP tools (gated behind `open_kitchen`/`close_kitchen` via FastMCP v3 tag-based visibility) and bundled skills registered as `/autoskillit:*` slash commands.

## **2. General Principles**

  * **Follow the Task Description**: The issue or ticket is your primary source of truth.
  * **Adhere to Task Scope**: Do not work on unassigned features or unrelated refactoring.
  * **Implement Faithfully**: Produce functionally correct implementations. Do not add unrequested features.
  * **Adhere to Project Standards**: Write clean, maintainable Python following established conventions.

## **3. Critical Rules - DO NOT VIOLATE**

### **3.0. Skill Invocations Are Orders**

  * When a message includes a `/skill-name`, execute it via the Skill tool **BEFORE** any other action. No exceptions.
  * Never skip or substitute a skill invocation based on your own judgment.

### **3.1. Code and Implementation**

  * **Do Not Oversimplify**: Implement logic with required complexity. No shortcuts that compromise correctness.
  * **Respect the Existing Architecture**: Build on established patterns. Understand existing code before modifying.
  * **Address the Root Cause**: Debug to find and fix root causes. No hardcoded workarounds.
  * **No Backward Compatibility Hacks**: No comments about dead code. Remove dead code entirely.
  * **Avoid Redundancy**: Do not duplicate logic or utilities.
  * **Use Current Package Versions**: Web search for current stable versions when adding dependencies.
  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-versions && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry.
  * **Hook Renames**: Renaming a hook script under `src/autoskillit/hooks/` must update `HOOK_REGISTRY` in `hook_registry.py` AND add the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit. `test_no_retired_name_has_a_live_file` will fail otherwise.
  * **Grep tool uses ripgrep (ERE) syntax**: Use `|` for OR-alternation in Grep tool `pattern`
    arguments. `\|` is Bash grep BRE syntax — ripgrep treats it as a literal backslash-pipe
    and returns 0 results. Example: `Grep(pattern="foo|bar")` not `Grep(pattern="foo\|bar")`.
  * **Worktree Init Prohibition**: Never run `autoskillit init` from within a git worktree. `sync_hooks_to_settings()` will raise `RuntimeError` if `pkg_root()` resolves to a worktree. Use `task install-worktree` for worktree setup — it does NOT call `init`.
  * **Naming convention — `*Def` vs `*Spec` suffixes**:
    - `*Def` — static definition of a registered entity (e.g., `HookDef`, `PackDef`, `FeatureDef`). Typically a `NamedTuple` or `@dataclass(frozen=True)`, used as elements in a registry or lookup table. Lives in `core/`.
    - `*Spec` — behavioral specification or validation rule (e.g., `RuleSpec`, `ExperimentTypeSpec`, `WriteBehaviorSpec`). Typically a `@dataclass` or `TypedDict` configuring a pipeline or validation stage. Lives in `recipe/` or domain layers.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `.autoskillit/temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

### **3.3. CLAUDE.md Modifications**

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

### **3.4. GitHub API Call Discipline**

  * **Batch inline review comments** via `POST /pulls/{N}/reviews` with `comments[]` array — never post comments individually unless the batch call fails.
  * **Batch GraphQL mutations** via aliases (N mutations in 1 request = 5 pts total, not N × 5 pts). Use for thread resolution, bulk PR queries, and any operation touching multiple entities.
  * **Delay 1s between POST/PATCH/PUT/DELETE calls** — add `sleep 1` (in shell) or `await asyncio.sleep(1)` (in Python) between consecutive mutating GitHub API calls.
  * **Pre-fetch entity lists** upfront in a single call; pass results via manifest files or variables rather than querying per-entity.
  * **Use `--json` field selection** to request only needed fields from `gh` CLI commands.
  * **Prefer GraphQL** for multi-entity reads — alias queries cost 1 point regardless of entity count.
  * **Never check response body for `comments` array length** after `POST /pulls/{N}/reviews` — GitHub does not echo back the comments array; HTTP 200 is the success signal.

### **3.5. GitHub Issue Body is the Source of Truth**

  * **Never use `gh issue comment`** to communicate issue status, triage feedback, tracking
    info, or occurrence data. Comments are not read by downstream consumers and fragment the
    record.
  * **All issue content updates must use `gh issue edit --body-file`**: fetch the current
    body, append the new section, write to `${{AUTOSKILLIT_TEMP}}`, then run
    `gh issue edit {number} --body-file "$FILE"`.
  * The `update_issue_body()` method on `GitHubFetcher` is the Python API equivalent.

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.
  * **Filtered tests**: `task test-filtered` runs path-filtered tests (defaults `AUTOSKILLIT_TEST_FILTER=conservative`). Set `AUTOSKILLIT_TEST_BASE_REF` to control the diff base. See `tests/CLAUDE.md` for filter modes and algorithm details.

## **5. Pre-commit Hooks**

Install hooks after cloning: `pre-commit install`. Run manually with `pre-commit run --all-files`.

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking, uv lock check, gitleaks secret scanning.

## **6. Architecture**

Top-level layout:

```
generic_automation_mcp/
├── assets/
├── docs/
├── scripts/
├── src/autoskillit/   # see below
├── tests/             # mirrors src/ layout; see tests/CLAUDE.md
├── Taskfile.yml
├── install.sh
└── pyproject.toml
```

`src/autoskillit/`:

```
├── __init__.py
├── __main__.py
├── _llm_triage.py           # Contract staleness triage (Haiku subprocess)
├── smoke_utils.py           # Callables for smoke-test pipeline run_python steps
├── hook_registry.py         # HookDef, HOOK_REGISTRY, generate_hooks_json
├── _test_filter.py          # Test filter manifest: glob-to-test-directory mapping
├── version.py               # Version health utilities (IL-0)
├── .claude-plugin/          # plugin.json
├── .mcp.json
│
├── core/                    # IL-0 foundation (zero autoskillit imports)
│   ├── __init__.py          #   Re-exports public surface
│   ├── io.py                #   atomic_write, ensure_project_temp, YAML helpers
│   ├── logging.py
│   ├── paths.py             #   pkg_root(), is_git_worktree()
│   ├── types.py             #   Re-export hub for _type_*.py
│   ├── _type_enums.py       #   StrEnums
│   ├── _type_subprocess.py
│   ├── _type_constants.py   #   GATED_TOOLS, FREE_RANGE_TOOLS, SKILL_TOOLS, etc.
│   ├── _type_results.py     #   LoadResult, SkillResult, FailureRecord, CleanupResult, etc.
│   ├── _type_protocols_logging.py   #   Protocols: AuditLog, TokenLog, TimingLog, McpResponseLog, GitHubApiLog, SupportsDebug, SupportsLogger
│   ├── _type_protocols_execution.py #   Protocols: TestRunner, HeadlessExecutor, OutputPatternResolver, WriteExpectedResolver
│   ├── _type_protocols_github.py    #   Protocols: GitHubFetcher, CIWatcher, MergeQueueWatcher
│   ├── _type_protocols_workspace.py #   Protocols: WorkspaceManager, CloneManager, SessionSkillManager, SkillLister, SkillResolver
│   ├── _type_protocols_recipe.py    #   Protocols: RecipeRepository, MigrationService, DatabaseReader, ReadOnlyResolver
│   ├── _type_protocols_infra.py     #   Protocols: GateState, BackgroundSupervisor, FleetLock, QuotaRefreshTask, TokenFactory, CampaignProtector
│   ├── _type_helpers.py
│   ├── _type_resume.py      #   ResumeSpec discriminated union: NoResume, BareResume, NamedResume
│   ├── _type_plugin_source.py #  PluginSource discriminated union: DirectInstall | MarketplaceInstall
│   ├── _linux_proc.py       #   read_boot_id, read_starttime_ticks — Linux /proc helpers (IL-0)
│   ├── _claude_env.py       #   IDE-scrubbing canonical env builder for claude subprocesses
│   ├── _terminal_table.py   #   IL-0 color-agnostic terminal table primitive
│   ├── _version_snapshot.py #   Process-scoped version snapshot for session telemetry (lru_cache'd)
│   ├── branch_guard.py
│   ├── claude_conventions.py #  Skill discovery directory layout constants
│   ├── github_url.py        #   parse_github_repo
│   ├── kitchen_state.py     #   Kitchen-open session marker (stdlib-only; readable from hooks)
│   ├── _plugin_cache.py     #   Plugin cache lifecycle: retiring cache, install locking, kitchen registry
│   ├── _plugin_ids.py       #   DIRECT_PREFIX, MARKETPLACE_PREFIX, detect_autoskillit_mcp_prefix (stdlib-only)
│   ├── _install_detect.py   #   is_dev_install() — editable-install detection for config resolution (IL-0)
│   ├── feature_flags.py     #   is_feature_enabled() — IL-0 feature gate resolution primitive
│   ├── readiness.py         #   Filesystem readiness sentinel primitives for MCP server startup (IL-0)
│   ├── session_registry.py  #   Session registry: maps autoskillit launch IDs to Claude Code session UUIDs
│   └── tool_sequence_analysis.py #  Cross-session tool call sequence DFG analysis (stdlib-only, IL-0)
│
├── config/                  # IL-1
│   ├── __init__.py
│   ├── defaults.yaml
│   ├── ingredient_defaults.py
│   ├── settings.py          #   AutomationConfig + schema validate/write API
│   ├── _config_dataclasses.py #  22 leaf dataclasses + ConfigSchemaError
│   └── _config_loader.py    #   _make_dynaconf + load_config layer helpers
│
├── pipeline/                # IL-1 pipeline state
│   ├── __init__.py
│   ├── audit.py             #   FailureRecord, DefaultAuditLog
│   ├── background.py        #   DefaultBackgroundSupervisor
│   ├── context.py           #   ToolContext DI container
│   ├── gate.py              #   DefaultGateState, gate_error_result
│   ├── github_api_log.py    #   DefaultGitHubApiLog — session-scoped GitHub API request accumulator
│   ├── mcp_response.py      #   Per-tool response size tracking
│   ├── telemetry_fmt.py     #   Canonical token/timing display
│   ├── timings.py
│   ├── tokens.py
│   └── pr_gates.py          #   is_ci_passing, is_review_passing, partition_prs
│
├── execution/               # IL-1
│   ├── __init__.py
│   ├── commands.py          #   Claude{Interactive,Headless}Cmd builders
│   ├── db.py                #   Read-only SQLite with defence-in-depth
│   ├── diff_annotator.py    #   Diff annotation + findings filter for review-pr
│   ├── headless.py          #   Headless Claude session orchestration (facade)
│   ├── _headless_recovery.py #  Recovery functions: _recover_from_separate_marker, _synthesize_from_write_artifacts, etc.
│   ├── _headless_path_tokens.py # Path-token extraction: _build_path_token_set, _extract_output_paths, _validate_output_paths
│   ├── _headless_result.py  #   Result building: _build_skill_result, _build_session_telemetry, _capture_failure, _apply_budget_guard
│   ├── _headless_git.py     #   Git helpers for LOC capture: _capture_git_head_sha, _parse_numstat, _compute_loc_changed
│   ├── _headless_scan.py    #   Write-path JSONL scanning (extracted from headless.py)
│   ├── linux_tracing.py     #   /proc + psutil process tracing (Linux)
│   ├── anomaly_detection.py #   Post-hoc anomaly detection over snapshots
│   ├── session_log.py       #   XDG-aware session diagnostics log writer
│   ├── recording.py         #   Record/replay subprocess runners via api-simulator
│   ├── process.py           #   Facade re-exporting from _process_*.py
│   ├── _process_io.py
│   ├── _process_jsonl.py
│   ├── _process_kill.py
│   ├── _process_monitor.py
│   ├── _process_pty.py
│   ├── _process_race.py     #   RaceAccumulator, resolve_termination
│   ├── quota.py             #   QuotaStatus, cache, check_and_sleep_if_needed
│   ├── ci.py                #   GitHub Actions CI watcher (httpx, never raises)
│   ├── merge_queue.py       #   GitHub merge queue watcher (facade)
│   ├── _merge_queue_classifier.py #  PRFetchState, ClassificationResult, ClassifierInconclusive, _classify_pr_state
│   ├── _merge_queue_group_ci.py #   _query_merge_group_ci, _QUERY, mutation strings — extracted from merge_queue.py for size budget
│   ├── _merge_queue_repo_state.py #  fetch_repo_merge_state, _text_has_push_trigger, _has_merge_group_trigger
│   ├── github.py            #   GitHub issue fetcher
│   ├── session.py           #   ClaudeSessionResult, extract_token_usage (facade)
│   ├── _retry_fsm.py        #   _KILL_ANOMALY_SUBTYPES, _is_kill_anomaly, _compute_retry
│   ├── _session_outcome.py  #   _compute_success, _compute_outcome
│   ├── _session_model.py    #   ContentState, ClaudeSessionResult, extract_token_usage, parse_session_result
│   ├── _session_content.py  #   _check_expected_patterns, _check_session_content, _evaluate_content_state
│   ├── remote_resolver.py   #   upstream > origin, clone-aware
│   ├── testing.py           #   Pytest output parsing + pass/fail adjudication
│   ├── clone_guard.py       #   Clone contamination guard — detect and revert direct changes to clone CWD
│   └── pr_analysis.py       #   extract_linked_issues, DOMAIN_PATHS, partition_files_by_domain
│
├── workspace/               # IL-1
│   ├── __init__.py
│   ├── cleanup.py           #   CleanupResult, preserve list
│   ├── clone.py             #   clone_repo + push_to_remote + DefaultCloneManager
│   ├── _clone_detect.py     #   detect_* helpers + RUNS_DIR + classify_remote_url
│   ├── _clone_remote.py     #   CloneSourceResolution + probe/isolate remotes
│   ├── session_skills.py    #   Per-session ephemeral skill dirs; subset filtering
│   ├── clone_registry.py    #   Shared file-based coordination for deferred cleanup
│   ├── skills.py            #   SkillResolver — bundled skill listing
│   └── worktree.py
│
├── planner/                 # IL-1
│   ├── __init__.py          #   Progressive resolution planner — re-exports expand_assignments, expand_wps, finalize_wp_manifest, validate_plan, compile_plan
│   ├── manifests.py         #   expand_assignments, expand_wps, finalize_wp_manifest, build_phase_assignment_manifest, build_phase_wp_manifest — manifest callables
│   ├── merge.py             #   merge_tier_dir, merge_files, build_plan_snapshot, extract_item, replace_item — JSON interchange merge tooling
│   ├── validation.py        #   validate_plan — DAG cycle check, structural completeness, sizing bounds, duplicate-deliverable detection
│   ├── schema.py            #   planner data contracts — PhaseResult, AssignmentResult, WPResult, PlanDocument TypedDicts
│   ├── compiler.py          #   compile_plan — topological sort, issue body generation, milestone definitions, plan artifacts
│   └── consolidation.py     #   consolidate_wps — post-elaboration WP consolidation: reads manifests, merges trivial WPs, rewrites dep IDs
│
├── recipe/                  # IL-2
│   ├── __init__.py
│   ├── contracts.py         #   Contract card generation + staleness triage
│   ├── io.py                #   load_recipe, list_recipes, iter_steps_with_context
│   ├── order.py             #   BUNDLED_RECIPE_ORDER — stable display order registry for Group 0 recipes
│   ├── loader.py            #   Path-based recipe metadata utilities
│   ├── _api.py              #   Orchestration API
│   ├── _cmd_rpc.py          #   run_python callables for externalized recipe cmd scripts
│   ├── _recipe_ingredients.py #  format_ingredients_table + LoadRecipeResult TypedDicts
│   ├── _recipe_composition.py #  _build_active_recipe + sub-recipe merging
│   ├── diagrams.py          #   Flow diagram generation + staleness detection
│   ├── experiment_type_registry.py #  ExperimentTypeSpec, load_all_experiment_types
│   ├── registry.py          #   RuleFinding, RuleSpec, semantic_rule decorator
│   ├── repository.py
│   ├── _analysis.py         #   ValidationContext + make_validation_context
│   ├── _analysis_graph.py   #   RouteEdge + build_recipe_graph + step graph primitives
│   ├── _analysis_bfs.py     #   bfs_reachable + symbolic BFS fact propagation
│   ├── _analysis_blocks.py  #   extract_blocks — group steps by block annotation
│   ├── _analysis_detectors.py #  dead outputs + ref invalidations + implicit handoffs
│   ├── rules_actions.py     #   Action-type semantic rules (stop-step-has-no-routing, recipe-has-terminal-step, route-step-requires-on-result)
│   ├── rules_blocks.py      #   Block-level budget rules (block-run-cmd-budget, etc.)
│   ├── rules_bypass.py
│   ├── rules_campaign.py
│   ├── rules_ci.py
│   ├── rules_clone.py
│   ├── rules_cmd.py
│   ├── rules_contracts.py
│   ├── rules_dataflow.py
│   ├── rules_features.py
│   ├── rules_fixing.py
│   ├── rules_graph.py
│   ├── rules_inline_script.py  #   inline-script-in-cmd + inline-python-in-cmd lint rules
│   ├── rules_inputs.py
│   ├── rules_isolation.py
│   ├── rules_merge.py
│   ├── rules_packs.py
│   ├── rules_reachability.py  #   Symbolic reachability rules (capture-inversion-detection, event-scope-requires-upstream-capture)
│   ├── rules_recipe.py
│   ├── rules_skill_content.py
│   ├── rules_skills.py
│   ├── rules_temp_path.py
│   ├── rules_tools.py
│   ├── rules_verdict.py
│   ├── rules_worktree.py     #   Semantic validation rule modules
│   ├── _git_helpers.py      #   Shared git-remote regex (_GIT_REMOTE_COMMAND_RE, _LITERAL_ORIGIN_RE) for lint rules
│   ├── _skill_helpers.py        #   Shared helpers for skill-related semantic rules
│   ├── _skill_placeholder_parser.py
│   ├── identity.py          #   Recipe identity hashing — content and composite fingerprints
│   ├── schema.py            #   Recipe, RecipeStep, DataFlowWarning
│   ├── staleness_cache.py
│   └── validator.py         #   validate_recipe, analyze_dataflow
│
├── migration/               # IL-2
│   ├── __init__.py
│   ├── engine.py            #   MigrationEngine, adapter ABC hierarchy
│   ├── _api.py              #   check_and_migrate
│   ├── loader.py            #   Migration note discovery + version chaining
│   └── store.py             #   FailureStore (JSON, atomic writes)
│
├── fleet/                   # IL-2
│   ├── __init__.py          #   Re-exports: CampaignSummary, parse_campaign_summary, etc.
│   ├── _api.py              #   Fleet campaign execution engine — dispatches L2 sessions, resolves campaign/result variable references
│   ├── _prompts.py          #   Prompt builder for L2 fleet dispatch sessions — assembles sous-chef instruction block from SKILL.md sections
│   ├── result_parser.py     #   L2 result block parser with Channel B JSONL fallback
│   ├── sidecar.py           #   Per-issue JSONL sidecar — IssueSidecarEntry, append/read/compute_remaining helpers
│   ├── _liveness.py         #   is_dispatch_session_alive() — boot_id + starttime_ticks liveness gate
│   ├── _semaphore.py        #   FleetSemaphore — configurable asyncio.BoundedSemaphore implementing FleetLock
│   ├── _sidecar_rpc.py      #   run_python-callable entry points: write_sidecar_entry, get_remaining_issues
│   ├── _findings_rpc.py     #   run_python-callable entry points: parse_and_resume, load_execution_map
│   ├── state.py             #   Campaign state persistence — DispatchRecord, DispatchStatus, atomic writes, resume algorithm
│   └── summary.py           #   Campaign summary schema v1: frozen dataclasses, sentinel parser, validator
│
├── server/                  # IL-3 FastMCP server
│   ├── __init__.py          #   FastMCP app, kitchen gating, headless tool reveal
│   ├── git.py               #   Merge workflow for merge_worktree
│   ├── _editable_guard.py   #   Pre-deletion editable install guard (stdlib-only)
│   ├── _guards.py           #   Tier/gate guard functions: _require_enabled, _require_orchestrator_*, _require_fleet, _check_dry_walkthrough, _validate_skill_command
│   ├── _lifespan.py         #   FastMCP lifespan: recorder teardown on shutdown
│   ├── _session_type.py     #   Session-type tag visibility dispatcher (3-branch startup logic)
│   ├── _wire_compat.py      #   Claude Code wire-format sanitization middleware
│   ├── _notify.py           #   _notify, track_response_size, _get_ctx_or_none
│   ├── _misc.py             #   Quota/hook/triage utilities + re-exports for tools_*.py layer compliance
│   ├── tools_kitchen.py     #   open_kitchen, close_kitchen + recipe:// resource
│   ├── tools_ci.py          #   set_commit_status + check_repo_merge_state
│   ├── tools_ci_watch.py    #   wait_for_ci + get_ci_status + _auto_trigger_ci
│   ├── tools_ci_merge_queue.py #  toggle_auto_merge + enqueue_pr + wait_for_merge_queue
│   ├── tools_clone.py
│   ├── tools_execution.py   #   run_cmd, run_python, run_skill
│   ├── tools_git.py         #   merge_worktree, classify_fix, etc.
│   ├── tools_recipe.py
│   ├── tools_status.py      #   kitchen_status, reports, summaries, quota events, read_db
│   ├── tools_github.py      #   fetch_github_issue, get_issue_title, report_bug
│   ├── tools_issue_lifecycle.py #  prepare/enrich/claim/release issue
│   ├── tools_pr_ops.py      #   get_pr_reviews, bulk_close_issues
│   ├── tools_workspace.py   #   test_check, reset_test_dir, reset_workspace
│   ├── _factory.py          #   Composition Root: make_context()
│   └── _state.py            #   Lazy init, plugin dir resolution
│
├── cli/                     # IL-3 CLI
│   ├── __init__.py
│   ├── _ansi.py             #   supports_color, NO_COLOR/TERM=dumb
│   ├── _terminal.py         #   terminal_guard() TTY restore
│   ├── _terminal_table.py   #   Re-export shim from core/_terminal_table
│   ├── _cook.py             #   cook: ephemeral skill session launcher
│   ├── _fleet.py            #   fleet subcommand group: status --reap/--dry-run, run stub; render_fleet_error() (facade)
│   ├── _fleet_display.py    #   Fleet status display: _STATUS_COLUMNS, _render_status_display, _watch_loop, _build_status_rows
│   ├── _fleet_lifecycle.py  #   Fleet lifecycle: _fleet_signal_guard, _reap_stale_dispatches
│   ├── _fleet_session.py    #   Fleet session launcher: _launch_fleet_session
│   ├── _reload.py           #   consume_reload_sentinel: reload sentinel detection for re-launch loops
│   ├── _restart.py          #   perform_restart() -> NoReturn: sets SKIP_UPDATE_CHECK, calls os.execv
│   ├── _session_launch.py   #   _run_interactive_session: shared interactive session launch prelude
│   ├── _doctor.py           #   Facade: DoctorResult, run_doctor(); delegates to sub-modules
│   ├── _doctor_types.py     #   DoctorResult dataclass, _NON_PROBLEM frozenset (shared types)
│   ├── _doctor_mcp.py       #   MCP server registration + plugin cache checks
│   ├── _doctor_hooks.py     #   Hook registration, registry drift, and health checks
│   ├── _doctor_install.py   #   Install path, entry points, version drift, update dismissal checks
│   ├── _doctor_config.py    #   Project config, gitignore, secret scanning checks
│   ├── _doctor_runtime.py   #   Quota cache schema + claude process state checks
│   ├── _doctor_env.py       #   Ambient session type + campaign ID env checks
│   ├── _doctor_features.py  #   Feature dependency + registry consistency checks
│   ├── _doctor_fleet.py     #   Fleet infrastructure, campaign state, sous-chef checks
│   ├── _hooks.py            #   PreToolUse hook registration helpers
│   ├── _init_helpers.py
│   ├── _installed_plugins.py #  InstalledPluginsFile — canonical accessor for installed_plugins.json
│   ├── _install_info.py     #   InstallInfo, InstallType, detect_install(), comparison_branch(), dismissal_window(), upgrade_command()
│   ├── _marketplace.py      #   Plugin install/upgrade
│   ├── _mcp_names.py        #   MCP prefix detection
│   ├── _onboarding.py       #   First-run detection + guided menu
│   ├── _prompts.py          #   Orchestrator prompt builder
│   ├── _timed_input.py      #   timed_prompt() and status_line() CLI primitives
│   ├── _menu.py             #   Shared numbered selection menu primitive: run_selection_menu(), render_numbered_menu(), resolve_menu_input()
│   ├── _update.py           #   run_update_command(): first-class upgrade path for `autoskillit update`
│   ├── _update_checks.py    #   Unified startup update check: version/hook/source-drift signals, branch-aware dismissal (facade)
│   ├── _update_checks_fetch.py #  HTTP cache + fetch machinery: _fetch_with_cache, _fetch_latest_version, invalidate_fetch_cache
│   ├── _update_checks_source.py # Source-repo discovery + SHA resolution: find_source_repo, resolve_reference_sha
│   ├── _serve_guard.py      #   Async signal-guarded MCP server bootstrap (extracted from app.py)
│   ├── _features.py         #   features subcommand group: list/status commands for feature gate inspection
│   ├── _workspace.py        #   Workspace clean helpers
│   ├── _session_picker.py   #   Scoped resume picker: filters sessions by type (cook/order) via registry + heuristic
│   ├── _sessions.py         #   sessions analyze CLI subcommand for cross-session DFG visualization
│   ├── _order.py            #   order command + helpers: _recipes_dir_for, _get_subsets_needed, _get_packs_needed
│   └── app.py               #   CLI entry: serve, init, config, skills, recipes, doctor, update, etc.
│
├── hooks/                   # Claude Code PreToolUse/PostToolUse/SessionStart scripts
│   ├── __init__.py
│   ├── hooks.json           #   Plugin hook registration
│   ├── branch_protection_guard.py
│   ├── _hook_settings.py    #   Shared stdlib-only settings resolver for quota guard hooks
│   ├── quota_guard.py       #   Blocks run_skill when threshold exceeded
│   ├── quota_post_hook.py   #   Appends quota warning to run_skill output
│   ├── remove_clone_guard.py
│   ├── skill_cmd_guard.py
│   ├── skill_command_guard.py
│   ├── ask_user_question_guard.py #  Blocks AskUserQuestion if kitchen is not open
│   ├── open_kitchen_guard.py
│   ├── unsafe_install_guard.py
│   ├── planner_gh_discovery_guard.py #  Blocks GitHub discovery commands in planner skill sessions
│   ├── pr_create_guard.py       #  Blocks gh pr create via run_cmd when kitchen is open
│   ├── generated_file_write_guard.py
│   ├── recipe_write_advisor.py  #   Advisory: Write/Edit to recipe YAMLs → suggests /autoskillit:write-recipe (non-blocking)
│   ├── grep_pattern_lint_guard.py #  Denies Grep calls with \\| BRE alternation; returns corrected ERE pattern
│   ├── mcp_health_guard.py  #   Detects MCP server disconnect via PID liveness; injects /MCP reconnect hint
│   ├── leaf_orchestration_guard.py
│   ├── fleet_dispatch_guard.py #  Blocks dispatch_food_truck from headless callers (L3→L3 recursion guard)
│   ├── review_gate_post_hook.py #  PostToolUse: writes/clears review_gate_state.json on run_skill gate tags and check_review_loop calls
│   ├── review_loop_gate.py  #   PreToolUse: blocks wait_for_ci/enqueue_pr when LOOP_REQUIRED gate is active and check_review_loop not yet called
│   ├── pretty_output_hook.py #  Dispatch entrypoint for MCP JSON → Markdown-KV reformatter
│   ├── _fmt_primitives.py   #   Payload dataclasses, token formatter, pipeline-mode + short-name
│   ├── _fmt_execution.py    #   run_skill, run_cmd, test_check, merge_worktree formatters
│   ├── _fmt_status.py       #   token/timing summary, kitchen_status, clone_repo formatters
│   ├── _fmt_recipe.py       #   load_recipe, open_kitchen, list_recipes formatters
│   ├── token_summary_hook.py #  Appends Token Usage Summary to PR body
│   └── session_start_hook.py #  Injects open-kitchen reminder on resume
│
├── migrations/              # Versioned migration YAML notes
├── recipes/                 # Bundled recipe YAML + contracts/, diagrams/, sub-recipes/
├── skills/                  # Tier 1: open-kitchen, close-kitchen, sous-chef
└── skills_extended/         # Tier 2 (interactive) + Tier 3 (pipeline/automation) skills
                             # incl. arch-lens-* (13), exp-lens-* (18), and vis-lens-* (12) diagram families
```

**Session diagnostics logs** live at `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Override with `linux_tracing.log_dir`. Session directories are named by Claude Code session UUID when available (parsed from stdout, or discovered from JSONL filename via Channel B). Fallback: `no_session_{timestamp}`. Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.

**CRITICAL**: When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.

**Import layer vs. orchestration level:** Module docstrings and import-linter
contracts use IL-N labels (IL-001–IL-009 in `pyproject.toml`) for the import
dependency hierarchy — these are separate from the L0–L3 orchestration levels
defined in `docs/orchestration-levels.md`.

## 7. Session Diagnostics

**Path components use hyphens, not underscores.** Log directory names and session folder names are hyphen-separated. Never assume underscores when constructing or searching for log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).
