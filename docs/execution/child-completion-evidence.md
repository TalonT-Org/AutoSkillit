# Async Child Completion Lifecycle — AC1-AC8 Evidence (issue #4233)

Cumulative remediation evidence recorded against the
`impl-4233-remediation2-20260711-152527` worktree. Each acceptance
criterion maps to one or more production changes and one or more named
tests. Live Claude smoke never substitutes for deterministic evidence.

| AC | Behaviour | Production change | Named test(s) | Status |
|----|-----------|-------------------|---------------|--------|
| AC1 | Production parsing/provenance — Channel A emits lifecycle evidence with exact raw-byte provenance per complete record. | `_channel_a_pump.py` splits raw bytes, calculates offsets from raw line lengths, stamps observation / parent_marker / lifecycle_issue byte_offset; `_claude_lifecycle.py` emits `extract_lifecycle_issues`; `SessionEvent.lifecycle_issues` and `ChildLifecycleSnapshot.lifecycle_issues` carry them through. | `tests/execution/test_lifecycle_actor.py`, `tests/execution/backends/test_claude_lifecycle_normalization.py`, `tests/execution/backends/test_claude_stream_parser.py`. | wired |
| AC2 | Active-child deferral — fresh parent candidates are deferred while child obligations remain active or awaiting delivery. | `ChildLifecycleCoordinator.evaluate_candidate` blocks eligibility on `_attempts`, `_awaiting_delivery`, and `_unresolved_terminal` non-empty. `AWAITING_DELIVERY` added to `ChildObligationState`. | `tests/execution/test_child_lifecycle_coordinator.py::TestCandidateEvaluation`, `tests/execution/test_child_lifecycle_coordinator.py::TestDelivery`. | wired |
| AC3 | Terminal / replacement / finalization — failed/cancelled/timed-out work is irreversible until a natively linked replacement is delivered; `_OwnedProcessFinalizer` is the sole single-flight shielded cleanup authority with `unknown_identities` tracking. | `ChildLifecycleCoordinator.observe` applies proven native replacement edges before rejecting updates to an unresolved-terminal record; `CleanupOutcome` carries `unknown_identities` for unclassifiable survivors. | `tests/execution/test_child_lifecycle_coordinator.py::TestUnresolvedTerminalRetention`, `tests/execution/test_termination_executor.py`, `tests/execution/test_process_identity.py`. | wired |
| AC4 | Preserved `ScheduleWakeup` policy — no scheduler / extension / ordering change is introduced. | `_watch_child_activity` injection parameter with `ProcessActivityTracker`; module-global `_default_activity_tracker` and `_has_active_child_processes` removed; fresh tracker injected at every production call site. | `tests/execution/test_process_deadline_extension.py`, `tests/execution/test_process_session_log_monitor_stale_suppression.py`. | wired |
| AC5 | Five-child survival/progress — five descendants prove observable progress after an early marker, terminal plus parent delivery for all obligations, a distinct later parent marker for completion, and death of every captured identity. | `tests/execution/test_process_child_lifecycle_integration.py::test_production_runner_defers_early_marker_until_five_children_finish` covers the real-process replay; `ProcessActivityTracker` is invocation-scoped with no module-global singleton. | `tests/execution/test_process_child_lifecycle_integration.py::test_production_runner_defers_early_marker_until_five_children_finish`. | wired |
| AC6 | Fast empty-marker / no-child completion — bounded natural completion when `completion_marker == ""`. | `_headless_execute.py` preserves the deliberate non-lifecycle path: no marker-aware factory, no actor/producers, prior bounded natural/no-child behavior. | `tests/execution/test_process_child_lifecycle_integration.py::TestNoChildFastPath`, `tests/execution/test_process_run.py`. | wired |
| AC7 | Real tracked implementation-worktree write/parity — `run_skill` writes a tracked source file in a linked worktree and pre/post SHA, branch, marker, and write evidence are all asserted. | `tests/server/test_run_skill_async_child_lifecycle.py::test_run_skill_waits_for_child_then_records_tracked_write` exercises the full `run_skill -> DefaultHeadlessExecutor -> ClaudeCodeBackend -> DefaultSubprocessRunner` path. | `tests/server/test_run_skill_async_child_lifecycle.py`. | wired |
| AC8 | Passing `task test-check` and `task test-all` from the anchored repository root, plus pre-commit clean. | `task test-check` and `task test-all` are the deterministic repository gate; `pre-commit run --all-files` runs ruff + mypy + uv-lock + secrets + AGENTS.md / `__init__.pyi` integrity checks. | `task test-check`, `task test-all`, `pre-commit run --all-files`. | pending |

## Repository-gate evidence

| Gate | Command | Result |
|------|---------|--------|
| Repository gate (deterministic) | `task test-check` | pending |
| Repository gate (full lint + tests) | `task test-all` | pending |
| Pre-commit (ruff format / ruff check / mypy / uv-lock / secrets) | `pre-commit run --all-files` | pending |

Live Claude smoke (`test-smoke-claude`) is credential-gated and
supplementary; it never substitutes for deterministic evidence. Its
probes require explicit opt-in, `claude` on `PATH`, and verified
authentication via a supported API-key environment variable or
unexpired `claude auth status --json` OAuth status.

## Type additions (this remediation pass)

| Type | Module | Purpose |
|------|--------|---------|
| `CandidateSighting` | `core/types/_type_lifecycle.py` | Primitive-only frozen per-channel sighting value for completion candidates |
| `AWAITING_DELIVERY` | `ChildObligationState` | Obligation state for children with terminal process evidence but no delivery |
| `unknown_identities` | `CleanupOutcome` | Identities that could not be classified during cleanup verification |
| `awaiting_delivery` | `ChildLifecycleSnapshot` | Snapshot field for children in awaiting-delivery state |
| `sightings` | `CompletionCandidate` | Per-channel provenance tuple for A/B offset non-interchangeability |
