# execution/

Subprocess integration, headless session, process lifecycle, and session result tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Shared fixtures and helpers for tests/execution/ |
| `test_anomaly_detection.py` | Tests for post-hoc anomaly detection over ProcSnapshot data |
| `test_check_repo_merge_state.py` | Round-trip budget tests for fetch_repo_merge_state |
| `test_ci.py` | L1 unit tests for execution/ci.py — CIWatcher service |
| `test_ci_params.py` | Tests for CIRunScope query param composition and workflow scoping |
| `test_clone_guard.py` | Tests for clone contamination guard — detect and revert direct changes |
| `test_commands.py` | Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders |
| `test_db.py` | L1 unit tests for execution/db.py — SQL validation and authorizer |
| `test_diff_annotator.py` | Behavioral tests for execution/diff_annotator.py |
| `test_exit_classification.py` | Unit tests for classify_infra_exit and InfraExitCategory enum |
| `test_flag_contracts.py` | Contract tests for Claude CLI flags |
| `test_flush_completeness_guard.py` | Structural guard: every required-container field must appear in flush output |
| `test_flush_provider_integration.py` | Integration seam tests: provider fields forwarded from _execute_claude_headless to flush_session_log |
| `test_github.py` | L1 unit tests for execution/github.py |
| `test_github_api_tracking_http.py` | GitHub API tracking HTTP tests |
| `test_github_headers.py` | Tests for the shared github_headers helper and its adoption by all three classes |
| `test_headless_add_dirs.py` | Tests for run_headless_core multi-path --add-dir support (T-OVR-012..013) |
| `test_headless_core.py` | Tests for headless_runner.py extracted helpers |
| `test_headless_debug_logging.py` | Tests for debug logging instrumentation in headless.py |
| `test_headless_dispatch.py` | Tests for headless.py dispatch flow: food truck dispatch, pack injection, executor protocol |
| `test_headless_env_injection.py` | Phase 2 tests: AUTOSKILLIT_HEADLESS=1 env var injection in headless.py |
| `test_headless_env_scrub.py` | Launch-site env-scrub contract test for run_headless_core |
| `test_headless_ordering.py` | AST-based structural test for post-session operation ordering in headless.py |
| `test_headless_path_validation.py` | Tests for headless.py: _build_skill_result, path validation, synthesis, and contract gates |
| `test_headless_provider_fallback.py` | Tests for the provider fallback loop in _execute_claude_headless — STALE and BUDGET_EXHAUSTED trigger provider switch |
| `test_headless_provider_forwarding.py` | Tests verifying provider_extras and profile_name forwarding through the headless call chain |
| `test_headless_result_write_reconciliation.py` | Integration tests for EMPTY_OUTPUT + write-evidence reconciliation gate in _build_skill_result |
| `test_headless_synthesis.py` | Tests for headless.py synthesis helpers: output path extraction, validation, contamination |
| `test_idle_output_env.py` | Group G (execution part): AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT env variable injection tests |
| `test_linux_tracing.py` | Tests for Linux-only process tracing via psutil and /proc filesystem |
| `test_linux_tracing_pty_integration.py` | Integration test: PTY-wrapped command is traced at the workload level, not the wrapper |
| `test_loc_capture.py` | Tests for LoC capture helpers in execution.headless (T-GIT-1..T-GIT-6) |
| `test_merge_queue_classifier.py` | Tests for merge queue classifier: PendingCIGuard, InconclusiveBudget, ClassifierImmunity, VocabularyContract |
| `test_merge_queue_ejection.py` | Tests for merge queue ejection: RelatedCoverage, EjectionEnrichment, FetchRepoMergeStateRetry |
| `test_merge_queue_polling.py` | Tests for DefaultMergeQueueWatcher polling state machine |
| `test_normalize_subtype.py` | Unit tests for ClaudeSessionResult.normalize_subtype() normalization gate |
| `test_on_spawn_timing.py` | Tests for on_pid_resolved callback timing in run_managed_async (Group J) |
| `test_output_format_contract.py` | Contract tests binding output format to data availability |
| `test_pr_analysis.py` | Tests for execution/pr_analysis.py |
| `test_process_channel_b.py` | Integration tests for Channel B drain-race and COMPLETED pipeline adjudication |
| `test_process_debug_logging.py` | Tests for debug logging instrumentation in process.py |
| `test_process_heartbeat.py` | Unit tests for _heartbeat, _has_active_api_connection, _has_active_child_processes, orphaned tool result detection |
| `test_process_idle_watchdog.py` | Tests for the stdout idle watchdog coroutine (_watch_stdout_idle) |
| `test_process_jsonl.py` | Tests for JSONL marker detection utilities |
| `test_process_kill.py` | Integration tests for process tree kill and async cancellation |
| `test_process_pty.py` | Tests for PTY wrapping and pipeline adjudication boundary tests |
| `test_process_race.py` | Unit tests for _process_race.py: resolve_termination and ChannelBStatus |
| `test_process_run.py` | Integration tests for normal subprocess run, stdin, timeout, temp I/O, and logging |
| `test_process_session_log_monitor.py` | Unit tests for _session_log_monitor and related session log monitoring behavior |
| `test_process_submodules.py` | Tests verifying process.py decomposition into focused sub-modules (P8-2) |
| `test_provider_outcome_container.py` | Tests for ProviderOutcome typed container construction — required fields and TypeError on omission |
| `test_push_trigger_applies.py` | Unit tests for _push_trigger_applies_to_branch and _has_merge_group_trigger |
| `test_quota_binding.py` | Tests for execution/quota.py — multi-window selection, per-window thresholds, cache refresh |
| `test_quota_http.py` | End-to-end HTTP tests for quota guard using api-simulator mock_http_server |
| `test_quota_io.py` | Tests for execution/quota.py — credential reading, cache I/O, dataclass validation |
| `test_quota_sleep.py` | Tests for execution/quota.py — check_and_sleep_if_needed, resets_at-None blocking |
| `test_readiness_helper_contract.py` | AST lint guard: no inline stderr/stdout readline loops used as subprocess readiness polls |
| `test_recording.py` | Tests for RecordingSubprocessRunner and related helpers |
| `test_resume_concurrency.py` | Tests for file lock preventing concurrent resume of same session |
| `test_resume_prompt.py` | Tests for _build_resume_context and build_skill_session_cmd resume integration |
| `test_recording_sigterm.py` | Integration test: autoskillit serve subprocess receives SIGTERM and writes scenario.json |
| `test_recording_sigterm_early_term.py` | Edge case: SIGTERM sent to subprocess before readiness sentinel appears |
| `test_recording_skills.py` | Tests for _recording_skills snapshot/restore helpers |
| `test_remote_resolver.py` | Unit tests for execution.remote_resolver.resolve_remote_repo and resolve_remote_name |
| `test_session_adjudication_outcome.py` | Tests for _compute_outcome, content state evaluation, and session adjudication consistency |
| `test_session_adjudication_retry.py` | Tests for _compute_retry, _is_kill_anomaly, and related retry adjudication logic |
| `test_session_adjudication_success.py` | Tests for _compute_success adjudication logic |
| `test_session_debug_logging.py` | Tests for debug logging instrumentation in session.py |
| `test_session_state_persistence.py` | Tests for persist_session_state, read_session_state, and clear_session_state |
| `test_session_log_fields.py` | Tests for flush_session_log field coverage: write warnings, kitchen/order IDs, crash exception, raw stdout, per-turn fields |
| `test_session_log_flush.py` | Tests for flush_session_log: directory structure, proc-trace, summary/index, resolve_log_dir, temporal fields |
| `test_session_log_integration.py` | Integration tests: full tracing pipeline (accumulation + flush) end-to-end |
| `test_session_log_retention.py` | Tests for recover_crashed_sessions and retention/campaign-protection logic |
| `test_session_model_peak_context.py` | Tests for peak_context and turn_count extraction from extract_token_usage |
| `test_session_parsing.py` | L1 unit tests for execution/session.py — token extraction, parsing, and SkillResult |
| `test_session_result.py` | L1 unit tests for ClaudeSessionResult and parse_session_result — result types and policies |
| `test_termination_action.py` | Unit tests for decide_termination_action — pure decision function |
| `test_termination_executor.py` | Integration tests for execute_termination_action |
| `test_testing.py` | L1 unit tests for execution/testing.py — pytest output parsing |
| `test_trace_target_resolver.py` | Tests for resolve_trace_target — descendant-walk and basename-match contract |
| `test_write_evidence.py` | Write evidence: multi-directory fs snapshot and write_watch_dirs plumbing |
| `test_write_evidence_invariants.py` | Write-evidence invariants: 'no work done' retry reasons must be overridden by write evidence |
| `test_zero_write_detection.py` | Contract: sessions expected to write must actually write (behavioral write-count gate) |

## Architecture Notes

`conftest.py` provides shared fixtures for the execution test suite. The headless tests are split across multiple files by concern (dispatch, synthesis, path validation, env injection, ordering) following the P1-F01 audit fix.
