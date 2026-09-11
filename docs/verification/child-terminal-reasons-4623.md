# Child Terminal Reasons Verification (#4623)

Date: 2026-09-11

This record separates what this implementation session verified deterministically
(fixtures, unit/integration tests, static contract checks) from the live
corpus/provider-cohort validation the plan's Step 8.2–8.4 describes, which this
session could not perform and does not claim to have performed.

## Captured CLI version manifest

Captured live in the Step 1 investigation (`.autoskillit/temp/child-terminal-reasons-4623/step1_investigation.md`,
gitignored, not part of this commit tree):

- Claude Code: `2.1.257`
- Codex CLI: `codex-cli 0.153.4`

## Implementation and test coverage

All 8 plan steps are implemented and committed on this branch (base `94e3d18e0`):

| Step | Commit | Summary |
|---|---|---|
| 1–2 | `32e9f34fb` | Stdlib-only snapshot authority (`hooks/_child_outcome_snapshot/`), mapping table, locking |
| 3 | `66f2f7b48` | Native Claude/Codex lifecycle hook (`hooks/lifecycle/child_outcome_hook.py`), registry entries |
| 4 | `ab11584be` | Execution-layer reader/collector (`execution/child_outcomes.py`), silent-child recovery |
| 5 | `c84bfa5f8` | `ManagedAttemptRecorder` — one durable row per physical managed-leaf provider attempt |
| 6.1, 6.3–6.6 | `eaa3ded66` | `SessionTelemetry.child_outcomes`, summary/index projection, reused-recovery refresh, schema v11 |
| 7.1, 7.3 | `1ce5feb80` | `DurableArtifactWriterDef` registration, diagnostics/observability docs |
| 6.2 | `855858aa3` | `AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR` producer/entrypoint (`_assemble_shared_env_extras`) |
| 8.5 | `e52b5b5da` | This validation record |
| — | `18ee003fb` | Extract managed-attempt wiring to stay under tracked size ratchets; one approved `PolicyRelaxationApproval` (`tests/arch/_acceptance_policy_surfaces.py`, `headless/_headless_execute.py` 711→738, issue #4623) |

## Open follow-up: `_headless_execute.py` size ratchet

`execution/headless/_headless_execute.py` was already at 709/711 lines against
its tracked facade-split budget before this issue touched it. Steps 5 and 6
add managed-attempt recording call sites embedded directly in exception
handlers and cancellation-handling control flow (nonlocal-closure state) that
could not be safely extracted further without restructuring that flow — a
materially riskier change to attempt under implementation time pressure. After
extracting what could be moved safely (749 → 738 lines, no behavior change),
the user reviewed and explicitly approved the remaining relaxation in-session
(`tests/arch/_acceptance_policy_surfaces.py`, one `PolicyRelaxationApproval`,
711 → 738, issue #4623). A further decomposition of that cancellation-handling
flow remains open and is not resolved by this relaxation.

Step 7.2 (`HOOK_REGISTRY` completeness, `FILE_COUNT_LIMITS`, `registry.sha256`) required
no further changes — verified already correct from steps 1–2/3 during a dedicated
re-exploration pass (registry entries, matcher handling, `docs/safety/hooks.md`
counts, and the registry hash were all already in sync; the hooks/ file-count
ceiling is unchanged at 27 because both new modules were placed in subdirectories
specifically to stay under it).

New/substantially extended test files: `tests/hooks/test_child_outcomes.py` (787
lines), `tests/execution/test_child_outcomes.py` (650 lines),
`tests/execution/test_headless_managed_attempt_recording.py` (443 lines, new) — 103
collected test cases across these three files alone, plus extensions to
`tests/execution/headless/test_headless_evidence_split_integrity.py`,
`tests/execution/test_session_log_backend_source.py`,
`tests/core/test_session_index_schema_version_lock.py`,
`tests/execution/backends/test_backend_cmd_builder_base.py`,
`tests/execution/backends/test_backend_env_injection.py`,
`tests/execution/test_headless_core.py`, `tests/_ambient_env_surface.py`, and
`tests/execution/conftest.py`'s shared `_flush` helper.

Verified passing (scoped runs during implementation; the full-suite gate is
recorded separately below): reason-fidelity/provider-invariance mapping
(context-exhaustion, turn-limit, error, interrupt, abandonment, unknown, with
adverse evidence outranking a success subtype); exactly-one-row identity under
duplicated/out-of-order/late-refining evidence; durability under a killed writer
and concurrent sibling writes; native Claude/Codex replay fixtures; managed
physical-attempt recording across two provider attempts (with and without a
lineage observer), cancellation before/after a confirmed spawn, and launch-alias
binding onto the same row; summary/index projection including the reused-recovery
refresh-in-place path (a dedicated test confirms it refines `unknown` to a known
reason in an already-committed `summary.json` and `sessions.jsonl` row while
leaving unrelated fields untouched); the `ChildOutcomeDict`/`ChildOutcomeWireDict`
field-parity contract; and the `AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR` env-var
authority/ambient-surface/interactive-exclusion contracts for both backends.

## Live-induction attempt (turn limit) — negative result, not fabricated

Step 1 attempted to induce a real `maxTurns` partial-output marking via a
temporary `.claude/agents/_step1-turnlimit-probe.md` custom subagent (removed
after the probe). The probe completed all 3 sequential Bash calls successfully
with no partial marking or truncation observed. **The exact structured/JSON
shape of the `maxTurns` partial marking, and of the foreground/background
API-error Agent-tool result beyond the documented literal string, could not be
captured live within this implementation session.** Per the plan's own Step 1.3
instruction, the implementation:

- Matches only the documented literal string `Agent terminated early due to an
  API error` (exact substring) as structured harness evidence for `error`.
- Does not implement a `turn_limited` classification path keyed to an
  uncaptured Agent-tool partial-marking shape for a *subagent* specifically.
  `turn_limited` is implemented for the explicit `error_max_turns` CLI subtype
  and existing parent-level `terminal_reason`/`cli_subtype` fields, which are
  real, structured, already-captured fields.

This mirrors existing repository precedent (`tests/fixtures/claude_code/api_error_404_terminal_v1.jsonl`,
`authentication_failed_v1.jsonl`, `weekly_rate_limit_rejected_v1.jsonl`;
`tests/fixtures/codex/turn_failed_error_v0133.ndjson`,
`turn_failed_model_capacity_v0133.ndjson`) for conditions that cannot be
live-captured: a focused structured-result fixture test, with the absent live
case named here rather than a fabricated capture.

## Step 8.2–8.4: live corpus and provider-cohort validation — out of scope for this session

The plan's Step 8.2 ("reuse the issue's archived parent/child population to
build a bounded inventory"), 8.3 (replay identical evidence across native and
MiniMax cohorts to prove zero reporting-driven difference), and 8.4 (publish
real per-role/provider reason distributions from the captured corpus) all
require either a fetchable archive of the issue's real historical session
population, or live multi-provider execution capability (a working MiniMax
provider credential/cohort) that this implementation session does not have
access to. No such population comparison or cross-provider replay was run, and
none is claimed here. This is the honest "absent live case" the plan's own
Step 1.3 instruction anticipates, not a gap silently passed over — a follow-up
session with access to the issue's archived corpus and a live MiniMax cohort
is required to close it.

## Test and pre-commit gate

`pre-commit run --all-files` passed at every commit on this branch (ruff
format, ruff check, mypy, and the REQ-CNST-010 diff-scoped file-length hard cap,
among the other configured hooks). The full `task test-check` gate result for
the complete branch is recorded at merge time; see the retry-worktree session's
own completion report for its pass/fail summary.
