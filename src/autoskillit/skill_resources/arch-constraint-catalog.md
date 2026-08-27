---
id: arch-constraint-catalog
title: Architectural Constraint Catalog
summary: Project-wide constraints that must be checked before accepting review suggestions.
---
**Architectural Constraint Catalog — consult before classifying ACCEPT:**

The following project-wide constraints are enforced by pytest tests across the test suite
(primarily `tests/arch/`, `tests/recipe/`, `tests/workspace/`, and `tests/server/`).
They are NOT caught by pre-commit. A suggestion that violates any of these must be
classified `REJECT` with `category: "arch_violation"`.

| Constraint | Enforced by | What is prohibited |
|---|---|---|
| Regex import | `test_regex_import.py` | `import re` or `from re import` in `src/` outside `hooks/` and `core/` — must use `import regex as re` |
| Atomic writes | `test_ast_rules.py` (REQ-AST-002) | `.write_text()` / `.write_bytes()` in `src/` — must use `_atomic_write()` |
| No print | `test_ast_rules.py` (ARCH-001) | `print()` in production `src/` code |
| No StrEnum-to-string compare | `test_ast_rules.py` (ARCH-010) | Comparing StrEnum fields to raw string literals |
| Dataclass slots | `test_dataclass_slots.py` | `dataclass(frozen=True)` decorator without `slots=True` |
| *Def/*Spec naming | `test_def_spec_naming.py` | `*Def` class that is not a `NamedTuple` or `@dataclass(frozen=True)`; `*Spec` class that is not a `@dataclass` or `TypedDict` |
| Import layer ordering | `test_layer_enforcement.py` | Importing from a higher IL layer (e.g., IL-2 recipe/ imported by IL-0 core/) |
| Anyio migration | `test_anyio_migration.py` | `asyncio.sleep`, `asyncio.Event`, `asyncio.to_thread` in `execution/process.py` |
| Protocol types on ToolContext | `test_subpackage_isolation.py` | Concrete class types instead of Protocol types for ToolContext service fields |
| Capability-based dispatch | `test_no_backend_name_bypass.py` | `if backend.name == "..."` — must use capability fields |
| Canonical maintenance argv | `test_maintenance_install_argv_contract.py` | Hand-built list or tuple argv containing `--maintenance-update` outside `MaintenanceInstallArgv.to_argv()` |
| step_name in run_cmd | `test_anti_pattern_guards.py` | Missing `step_name` in `run_cmd` `with:` blocks in recipe YAML |
| No hardcoded temp paths | `test_python_no_hardcoded_temp.py` | Literal `{{AUTOSKILLIT_TEMP}}` path string in Python outside whitelist |
| run_python path resolution | `test_run_python_path_resolution.py` | smoke_utils callable with relative-path fallback or path-like param not in `_PATH_LIKE_ARGS` |
| SkillResult kill_reason | `test_skill_result_construction_guard.py` | `SkillResult()` without `kill_reason=` kwarg |
| Never-raises contracts | `test_never_raises_contracts.py` | `mcp.tool()` handlers without top-level `try/except` and "Never raises" docstring |
| ClaudeFlags isolation | `test_backend_flag_isolation.py` | `ClaudeFlags` referenced in `_session_launch.py` — backend flags belong in backend layer only |
| Boot step symmetry | `test_boot_step_symmetry.py` | Both boot functions must call all required boot steps in the right order |
| BackendCapabilities consumed | `test_capability_consumption.py` | Every `BackendCapabilities` field must have a production consumer — unused capability fields are prohibited |
| Config fields consumed | `test_config_consumption.py` | Every config dataclass field must have a production read site — a key that only parses and validates itself is dead config |
| BackendCapabilities documented | `test_capability_docstrings.py` | `BackendCapabilities` class and every field must have docstrings |
| Env var symmetry | `test_env_symmetry.py` | `build_skill_session_cmd` and `build_food_truck_cmd` must both set required base env vars; `AGENT_BACKEND_ENV_VAR` must appear in food_truck |
| No NotImplementedError in backends | `test_no_not_implemented.py` | Registered backend classes must not raise `NotImplementedError` — `CodingAgentBackend` is a Protocol, not an ABC |
| Channel B timeout floor | `test_channel_b_timeout_guard.py` | Channel B calls with `timeout` below `TimeoutTier.CHANNEL_B` minimum |
| Clone network timeouts | `test_clone_timeouts.py` | `subprocess.run()` with git network subcommands (clone/fetch/pull/push/ls-remote) in `clone.py` without `timeout=` |
| Dispatch timeout resolver | `test_dispatch_timeout_guard.py` | `_run_dispatch` using hardcoded timeout instead of `resolve_dispatch_timeout()` |
| Doctor read-only | `test_doctor_readonly.py` | `run_doctor()` performing filesystem writes (REQ-DOCTOR-READONLY) |
| Persisted enum decoding | `test_persisted_enum_decoding.py` | Bare persisted-enum construction in registered decoders instead of tolerant construction or record quarantine |
| No requestId dedup in flush | `test_flush_no_rid_guard.py` | Inline `seen_request_ids` dedup in `session_log.py` or `tool_sequence_analysis.py` — dedup is pre-applied |
| GFM table rendering | `test_gfm_rendering_guard.py` | GFM table rendering bypassing `_render_gfm_table()` — all table output must route through it |
| CLI prompts via timed_prompt | `test_input_tty_contracts.py` | `input()` calls in `src/autoskillit/cli/` not routed through `timed_prompt()` |
| Interactive ordering gate | `test_interactive_ordering_gate.py` | Interactive launch sites that skip `assert_interactive_ordering()` before `_session_launch` |
| Kitchen guard scoping | `test_kitchen_guard_scoping.py` | `any_kitchen_open()` call sites not passing `project_path` — must use scoped check |
| Ambient home boundary | `test_ambient_home_boundary.py` | Raw `Path.home()` reads in registered managed-home modules outside their single approved resolution entry point |
| Model identity contract | `test_model_identity_contract.py` | `detect_model_drift()` using raw string comparison instead of `normalize_model_id()` and `_models_match()`, missing `profile_name` suppression guard with `normalize_model_id` normalization, or `profile_name` guard calling `_is_non_anthropic` more than once or on `configured_model` instead of `observed_model` (over-restriction that kills the guard for the standard Anthropic-configured + non-Anthropic-observed production path) |
| Recipe delivery provenance | `test_recipe_delivery_provenance.py` | Caller claims, host observations, selected outer limits, history retention, and measured byte ceilings must remain separately sourced |
| No hardcoded model IDs in translation tests | `test_no_hardcoded_model_ids_in_translation_tests.py` | String literal alias-resolved model IDs in `assert` comparisons in `test_model_translation.py` — assertions must reference `CODEX_MODEL_ALIASES[key]` to prevent co-authoring of wrong values |
| No error-dict returns | `test_no_error_dict_return.py` | `load_and_validate()` returning `{"error": ...}` dict — errors must propagate via exceptions |
| No hook tracker writes | `test_tracker_write_provenance.py` | Hook scripts referencing `pipeline_tracker` and performing file writes — step completion is server-authoritative, only `server/tools/` may mutate tracker state |
| No inline requestId dedup | `test_no_inline_jsonl_request_id_dedup.py` | `seen_request_ids` variable in `session_log.py` or `tool_sequence_analysis.py` |
| No Path.cwd() in server tools | `test_no_path_cwd_in_tools.py` | `Path.cwd()` in server tool handlers — use injected project path instead |
| No raw SIGTERM handler | `test_no_raw_signal_handler.py` | `signal.signal(SIGTERM, ...)` in `cli/app.py` — must use `anyio.open_signal_receiver` |
| PTY coherence | `test_pty_coherence.py` | Dispatch paths that allocate PTY without respecting dispatch-type `pty_override=False` |
| Quota capability isolation | `test_quota_capability_isolation.py` | `BackendCapabilities`, `.capabilities` access in quota modules — must use config string |
| Registry key casing | `test_registry_key_casing.py` | Uppercase keys in `FEATURE_REGISTRY`, `RETIRED_FEATURES`, or `PACK_REGISTRY` |
| Retired config key registry invariants | `test_retired_config_key_invariants.py` | `RETIRED_CONFIG_KEYS` entry reusing a retired name for a live field, a remap target that is not itself a currently-valid key (chained rename), or a remap touching a `_SECRETS_ONLY_KEYS` entry |
| Retired profile key registry invariants | `test_retired_profile_key_invariants.py` | `RETIRED_PROFILE_KEYS` entries that are not lowercase strings or reuse a live `ProviderProfileDef` field; retirement after the `raw_env` copy, dropping unrelated unknown profile keys, or failure to apply across every profile |
| Turn ID resolution | `test_resolve_turn_id_guard.py` | Direct `request_id` dict `.get()` outside `_resolve_turn_id()` — all turn ID resolution must go through the single resolver |
| No hardcoded @-mentions in SKILL.md | `test_skills_mention_guard.py` | `@word` tokens at word-boundary in SKILL.md prose — includes Python decorator examples (`@dataclass`, `@mcp`); use prose descriptions or remove the `@` prefix |
| FastMCP tag hygiene | `test_transforms_hygiene.py` | Test fixtures touching `mcp._transforms` without using the canonical `ALL_VISIBILITY_TAGS` constant |
| Watcher dispatch marker | `test_watcher_signal_consistency.py` | Process watchers that skip `_has_active_dispatch_marker()` check |
| Write restriction enforcement | `test_write_restriction_coverage.py` | Skills with prose write restrictions (`never modify source`, `read-only`, `output dir`) lacking runtime `WriteBehaviorSpec` enforcement |
| Subagent filter guard | `test_subagent_filter_guard.py` | NDJSON assistant-record consumers missing `_is_parent_assistant_record` or `_is_parent_assistant` predicate — subagent records contaminate parent metrics |
| Env-var-set constant consumption | `test_canonical_constant_consumption.py` | `*_ENV_FORWARD_VARS` or `*_REQUIRED_ENV` constant with zero production importers — every canonical env-var-set must be consumed |
| MCP env forward coverage | `test_mcp_env_forward_coverage.py` | `mcp_env_forward_vars` values missing from `CmdSpec.env` in any cmd-builder (skill, food-truck, headless, resume, interactive) |
| Rule severity consistency | `test_rule_severity_consistency.py` | Direct `RuleFinding()` construction in `@semantic_rule`/`@block_rule` bodies — must use `make_finding()`/`make_block_finding()`; `_KNOWN_NON_CONFORMING_RULES` entries without `# tracking: #NNNN` comments |
| Xfail bridge policy | `test_xfail_bridge_policy.py` | `xfail(strict=True)` decorators and `pytest.param` marks whose `reason` string does not cite a `#NNNN` tracking issue; `_XFAIL_POLICY_EXEMPT_FILES` entries without `# permanent:` or `# tracking:` rationale comments |
| No direct swap_labels in fleet | `test_swap_labels_guard.py` | Direct `swap_labels` calls in `fleet/` outside `_label_cleanup.py` — must route through `cleanup_orphaned_labels` |
| Issue URL extraction guard | `test_issue_url_extraction_guard.py` | Raw `.get("issue_url")` / `.get("issue_urls")` in `fleet/` outside `_issue_url_helpers.py` and `state_records.py` — must use `extract_issue_urls()`; `fleet_claim_guard.py` must retain both key variants |
| Pipeline ordering | `test_pipeline_ordering.py` | Moving `run_semantic_rules` before `_prune_skipped_steps` in `load_and_validate` — semantic rules must run on post-prune recipe |
| Hook env-var authority | `test_hook_env_var_authority.py` | Hook scripts that read `AUTOSKILLIT_PROVIDER_PROFILE` without also reading `AUTOSKILLIT_AGENT_BACKEND` — provider profile is a credentials label, not a backend-identity signal |
| Evidence-bound intake rules | `test_intake_rule_registry.py` | `CODEX_INTAKE_RULES` entries stating an absolute imperative without a declared `exception`; `basis`/`evidence`/`evidence_anchor` that does not resolve to a live backend capability, ADR, or issue; a `path_classes` entry naming a file class `recipe_read_guard` denies |
| Interpreter bytecode suppression | `test_interpreter_bytecode_suppression.py` | `sys.executable`/`python3` interpreter-spawn sites under `hooks/`, `hook_registry.py`, or `_codex_hooks.py` missing the `-B` flag — must suppress bytecode writes unconditionally |
| No ad-hoc env scrub duplication | `test_no_adhoc_env_workarounds.py` | New `monkeypatch.delenv(...)` / `os.environ.pop(...)` calls in tests for a var already scrubbed by conftest's `_scrub_ambient_env` autouse fixture, unless declared in `_INTENTIONAL_ENV_INPUT_SITES` |
| tools_execution facade completeness | `test_tools_execution_facade.py` | A `tools_execution` submodule (other than `__init__.py`) importing a cross-submodule or test-patched symbol directly via `from ... import name` instead of reading it through the package facade (`_te_pkg.name(...)`) — defeats `mock.patch("...tools_execution.<name>")`; also `locals()` calls in any submodule |
| No shared-root literal in install commands | `test_install_command_root_relocatability.py` | `cli/install/_install_info.py` string-constant literals naming an `environment_pinned_path_segments()` segment — install destinations must be built dynamically from `install_root_destination`, never a hardcoded shared/non-versioned path |

When a reviewer suggestion would cause a change matching any row above, classify
the finding as `REJECT` with `category: "arch_violation"` and `evidence` referencing
the specific constraint and enforcement test.


