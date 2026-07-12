# Async Child Completion Lifecycle — AC1-AC8 Evidence (issue #4233)

Cumulative remediation evidence recorded against the
`impl-4233-remediation2-20260711-152527` worktree. Each acceptance
criterion maps to one or more production changes and one or more named
tests. Live Claude smoke never substitutes for deterministic evidence.

| AC | Behaviour | Production change | Named test(s) | Status |
|----|-----------|-------------------|---------------|--------|
| AC1 | Production parsing/provenance — Channel A emits lifecycle evidence with exact complete-line byte provenance. | `_channel_a_pump.py` stamps observation / parent_marker / lifecycle_issue byte_offset; `_claude_lifecycle.py` emits `extract_lifecycle_issues`; `SessionEvent.lifecycle_issues` and `ChildLifecycleSnapshot.lifecycle_issues` carry them through. | `tests/execution/test_lifecycle_actor.py`, `tests/execution/backends/test_claude_lifecycle_normalization.py`, `tests/execution/backends/test_claude_stream_parser.py`. | wired |
| AC2 | Active-child deferral — fresh parent candidates are deferred while child obligations remain active. | `ChildLifecycleCoordinator.register_parent_marker` -> `_evaluate_candidates` blocks eligibility on `snapshot.has_active_children`. | `tests/execution/test_child_lifecycle_coordinator.py::TestCandidateEvaluation::test_active_children_block_eligibility`. | wired |
| AC3 | Terminal / replacement / finalization — failed/cancelled/timed-out work is irreversible until a natively linked replacement is delivered; `_OwnedProcessFinalizer` is the sole single-flight shielded cleanup authority. | `ChildLifecycleCoordinator.observe` applies proven native replacement edges before rejecting updates to an unresolved-terminal record; `note_child_work_failed` records irreversible failure; `CleanupOutcome.succeeded` precedence outranks child-work failure. | `tests/execution/test_child_lifecycle_coordinator.py::TestUnresolvedTerminalRetention`, `tests/execution/test_termination_executor.py`, `tests/execution/test_process_identity.py`. | wired |
| AC4 | Preserved `ScheduleWakeup` policy — no scheduler / extension / ordering change is introduced. | No change to `_watch_child_activity`'s extension logic beyond the new `activity_tracker` injection parameter; existing signature kept backwards compatible via the optional kwarg. | `tests/execution/test_process_child_lifecycle_integration.py::TestNoChildFastPath`, `tests/execution/test_process_session_log_monitor_stale_suppression.py`. | preserved |
| AC5 | Five-child survival/progress — five descendants prove observable progress after an early marker, terminal plus parent delivery for all obligations, a distinct later parent marker for completion, and death of every captured identity. | `tests/execution/test_process_child_lifecycle_integration.py::test_production_runner_defers_early_marker_until_five_children_finish` covers the real-process replay; `_process_monitor.ProcessActivityTracker` replaces the global cache so concurrent runs cannot leak CPU baselines. | `tests/execution/test_process_child_lifecycle_integration.py::test_production_runner_defers_early_marker_until_five_children_finish`. | wired |
| AC6 | Fast empty-marker / no-child completion — bounded natural completion when `completion_marker == ""`. | `_headless_execute.py` preserves the deliberate non-lifecycle path: no marker-aware factory, no actor/producers, prior bounded natural/no-child behavior. | `tests/execution/test_process_child_lifecycle_integration.py::TestNoChildFastPath`, `tests/execution/test_process_run.py`. | wired |
| AC7 | Real tracked implementation-worktree write/parity — `run_skill` writes a tracked source file in a linked worktree and pre/post SHA, branch, marker, and write evidence are all asserted. | `tests/server/test_run_skill_async_child_lifecycle.py::test_run_skill_waits_for_child_then_records_tracked_write` exercises the full `run_skill -> DefaultHeadlessExecutor -> ClaudeCodeBackend -> DefaultSubprocessRunner` path. | `tests/server/test_run_skill_async_child_lifecycle.py`. | wired |
| AC8 | Passing `task test-check` and `task test-all` from the anchored repository root, plus pre-commit clean. | `task test-check` and `task test-all` are the deterministic repository gate; `pre-commit run --all-files` runs ruff + mypy + uv-lock + secrets + AGENTS.md / `__init__.pyi` integrity checks. | `task test-check`, `task test-all`, `pre-commit run --all-files`. | recorded after Step 8 verification pass. |

## Repository-gate evidence

| Gate | Command | Result |
|------|---------|--------|
| Repository gate (deterministic) | `task test-check` | recorded after Step 8 verification pass. |
| Repository gate (full lint + tests) | `task test-all` | recorded after Step 8 verification pass. |
| Pre-commit (ruff format / ruff check / mypy / uv-lock / secrets) | `pre-commit run --all-files` | recorded after Step 8 verification pass. |

Live Claude smoke (`test-smoke-claude`) is credential-gated and
supplementary; it never substitutes for deterministic evidence. Its
probes require explicit opt-in, `claude` on `PATH`, and verified
authentication via a supported API-key environment variable or
unexpired `claude auth status --json` OAuth status.