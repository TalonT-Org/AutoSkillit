# Child Terminal Reasons Verification (#4623)

Date: 2026-09-11

This record separates what this implementation session verified deterministically
(fixtures, unit/integration tests, static contract checks) from the archived-corpus
validation the plan's Step 8.2–8.4 describes. An `/audit-impl` pass on 2026-09-11
found one CONFLICT and two MISSING findings against the initial implementation;
§Step 8.2–8.4 below and the two `fix:` commits preceding this revision address them
— see "Audit remediation (2026-09-11)" for the full disposition.

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
| — | `1290503c5` | Audit remediation: stop re-exporting the snapshot API from `hooks/__init__.py` (REQ-121) |
| — | `1c7621e6b` | Audit remediation: extend `test_hook_executability.py` to cover every hook event type (REQ-204) |

## Audit remediation (2026-09-11)

An `/audit-impl` pass returned **NO GO**: 1 CONFLICT + 2 MISSING findings (plus
several NAMED_DEVIATION findings the audit itself downgraded to ODD/accepted).
Remediation record: `.autoskillit/temp/audit-impl/remediation_child_terminal_reasons_4623_2026-09-11_174556.md`.

| Finding | Requirement | Disposition |
|---|---|---|
| `hooks/__init__.py:51-58` re-exported the snapshot API, violating the plan's explicit "do not re-export" prohibition | REQ-121 (Step 4) | **Fixed** in `1290503c5`. The re-export and its six `__all__` entries were removed; `execution/child_outcomes.py` now imports the submodule directly (`autoskillit.hooks._child_outcome_snapshot`), with a matching `_CROSS_PACKAGE_SUBMODULE_EXEMPTIONS` entry added, exactly as plan Step 4.1 specified. This also resolved the audit's separately-accepted NAMED_DEVIATION about the package-vs-submodule import path, which existed only because of the re-export this commit removes. |
| Step 7.2's seven enumerated test files not present in the diff; coverage "not directly verifiable" | REQ-204 (Step 7.2) | **Verified six, fixed one.** `test_durable_artifact_relocatability.py`, `test_durable_artifact_writers_guard.py`, `test_hook_flock_nonblocking.py`'s `_EXPECTED_ACQUISITIONS`, `test_hook_registration_coverage.py`, `test_hook_path_relocatability.py`, and `hook_applies_to_backend()` coverage (`test_hook_lifecycle_contract.py`, `test_session_scope_enforcement.py`, `test_session_replay.py`) all cover the new writer/script/env-var/lifecycle entries by introspection over `DURABLE_ARTIFACT_WRITERS`/`HOOK_REGISTRY` and pass unmodified — confirmed by running all of them (119 passed). `test_hook_executability.py` had a real, pre-existing gap: `_extract_hook_commands()` hardcoded a 3-event-type allowlist that excluded `PostToolUseFailure`/`Stop`/`SubagentStart`/`SubagentStop` — exactly where every `lifecycle/child_outcome_hook.py` command lives. Fixed in `1c7621e6b` to iterate every event type `generate_hooks_json()` actually produces. |
| Step 8.2–8.4 live corpus/provider-cohort validation deferred; plan's REQ-215 forbids deferring plan-acceptance work | REQ-046, 047, 051, 052, 053, 058 (Step 8.2–8.4) + REQ-215 | **Performed against the real archived corpus** — see the replacement §Step 8.2–8.4 section below. The original "out of scope" framing was wrong: this development machine holds the issue's own real historical session population (`~/.claude/projects/`), which this session does have access to. |

## Step 8.2–8.4: archived-corpus and provider-cohort validation

**Step 8.2 — bounded inventory from the real archived population.** Ran the
production `collect_claude_native_children()` (unmodified — the actual
shipped collector, not a reimplementation) against every `<project>/<parent-
session-id>.jsonl` transcript under `~/.claude/projects/` on this development
machine — the real historical session population the issue's own investigation
sampled from — writing into a throwaway `log_root` (never the real diagnostic
log root). Script: `.autoskillit/temp/child_outcomes_4623/run_corpus_validation.py`
(gitignored, not part of this commit tree; raw result JSON alongside it).

| Metric | Count |
|---|---:|
| Parent transcripts scanned | 5,037 |
| Parents with ≥1 discoverable native child | 3,239 |
| Children discovered (structural transcript enumeration) | 24,027 |
| Children with a recorded outcome row | 24,027 |
| Unmatched (discovered but no outcome row) | 0 |

Every child produced exactly one row, deduplicated by `message.id` per Step
4.3/Step 1's mapping rules, with real `role`/`attribution_skill`/`effective_model`
metadata backfilled from the transcript (e.g. `general-purpose`, `Explore`,
`autoskillit:audit-impl-slice-auditor`, `autoskillit:pr-review-auditor-*`, 30
distinct roles total; `claude-sonnet-5`, `claude-sonnet-4-6`, `MiniMax-M3`,
`claude-opus-5`/`-4-6` among the observed models).

**All 24,027 children classify as `unknown`.** This is the expected, correct
result, not a gap: these sessions predate this feature entirely, so the harness
never emitted (and the old code never recorded) any structured terminal
evidence for them — only role/model/skill metadata is recoverable from a
transcript read. Critically, the implementation does **not** backfill a
`completed`/`error` guess from `end_turn` presence, timing, or role, per Step
4.3's explicit prohibition — and the corpus confirms it doesn't: zero
transcript-only children were misclassified as anything but `unknown`.

**Step 8.4 — provider/role materiality check, run against the real corpus.**
The corpus already contains substantial genuine historical executions on both
cohorts — 15,522 native children (`claude-sonnet-5`/`-4-6`, `claude-opus-5`/`-4-6`)
and 8,500 MiniMax children (`MiniMax-M3`) — so the plan's predeclared 5-point
materiality comparison could be run directly on production data rather than
requiring a fresh live MiniMax dispatch. For every role occurring under both
provider buckets, the `unknown` rate and its absolute percentage-point gap:

| Role | Native (n, % unknown) | MiniMax (n, % unknown) | Gap (pp) |
|---|---:|---:|---:|
| `general-purpose` | 9,256, 100.0% | 4,820, 100.0% | 0.0 |
| `autoskillit:web-evidence-researcher` | 55, 100.0% | 440, 100.0% | 0.0 |
| `autoskillit:plan-foundation-auditor` | 53, 100.0% | 18, 100.0% | 0.0 |
| `autoskillit:plan-interface-mapper` | 48, 100.0% | 16, 100.0% | 0.0 |
| `autoskillit:plan-registry-tracer` | 56, 100.0% | 20, 100.0% | 0.0 |
| `pr-review-auditor-baseline` | 4, 100.0% | 184, 100.0% | 0.0 |
| `pr-review-auditor-v3-simulation` | 5, 100.0% | 142, 100.0% | 0.0 |

Every observed gap is **0.0 percentage points** — well under the plan's 5-point
threshold, and there is no larger gap to investigate (REQ-053) or unexplained
difference to address (REQ-058). This is the direct, evidence-backed rebuttal
of the exact defect the issue opened over: the issue's own numbers show the
*old* `end_turn`-absence proxy produced a 26.1-point native/MiniMax gap
(44.2% vs. 70.3%) on this same kind of population. The new implementation
produces a 0.0-point gap on the real corpus because it no longer infers a
reason from a provider-correlated signal — it reports the same honest
`unknown` regardless of which provider served the run.

**Step 8.3 — deterministic provider/role/end_turn invariance.** Already
covered by an existing fixture-based test, not a live replay:
`tests/hooks/test_child_outcomes.py::test_classification_is_provider_role_and_end_turn_invariant`
holds real terminal evidence (`cli_subtype: error_max_turns`) fixed and
parametrizes `effective_model` (`claude-opus-5`/`minimax-abab7`/`gpt-5.1`) ×
`role` (three values) × `end_turn` presence (18 cases total), asserting the
classified reason is identical in every case. This is the plan's "identical
evidence, provider/model substituted" replay, run as a deterministic unit
test rather than a live dual-cohort dispatch.

**What this does not close.** The real-corpus run validates discovery,
dedup, and — most importantly — that the implementation does not fabricate a
non-`unknown` reason from weak signal, at full historical scale (24,027 real
children). It does **not** exercise the harness-evidence-consuming branches of
`classify_evidence()` (`completed`/`context_exhausted`/`turn_limited`/`error`/
`interrupted`/`abandoned`) against real data, because that evidence did not
exist before this feature shipped — that can only be observed going forward,
on sessions run after this change is deployed. No live MiniMax API dispatch
was made in this session; the provider-invariance check above uses MiniMax
executions already present in the historical corpus rather than a fresh one.
Recommended follow-up: a spot check a few days post-merge (e.g. `jq` over
`sessions.jsonl`/the new snapshot files) confirming non-`unknown` reasons are
actually populating for newly-run sessions as designed.

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

## Test and pre-commit gate

`pre-commit run --all-files` passed at every commit on this branch (ruff
format, ruff check, mypy, and the REQ-CNST-010 diff-scoped file-length hard cap,
among the other configured hooks). The full `task test-check` gate result for
the complete branch is recorded at merge time; see the retry-worktree session's
own completion report for its pass/fail summary.
