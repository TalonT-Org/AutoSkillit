# Hooks

AutoSkillit registers 46 Claude Code hook scripts: 35 PreToolUse, 9 PostToolUse,
and 2 SessionStart. Every script is stdlib-only Python so it can run before the
project virtualenv is on the path. Scripts live in `src/autoskillit/hooks/`
and are bound to event types in `src/autoskillit/hook_registry.py` via the
`HOOK_REGISTRY` list of `HookDef` entries; `generate_hooks_json()` then
materializes the canonical `hooks.json` that Claude Code reads.

## PreToolUse hooks (35)

### `branch_protection_guard.py`
**Guarded tools:** `merge_worktree`, `push_to_remote`
Denies merges and pushes targeting branches in `safety.protected_branches`
(`main`, `develop`, `stable` by default). Pure-function check via
`core/branch_guard.is_protected_branch`.

### `quota_guard.py`
**Guarded tool:** `run_skill`
Blocks launching new headless sessions when the cached binding window marks
`should_block=True`, no explicit `AUTOSKILLIT_QUOTA_GUARD__DISABLED` override
is set, and no per-session disable marker exists. The threshold is per-window:
short windows (e.g. `five_hour`) use `quota_guard.short_window_threshold`
(default 85.0%); long windows matched by `quota_guard.long_window_patterns`
(default `weekly`, `sonnet`, `opus`) use `quota_guard.long_window_threshold`
(default 95.0%). Reports the exact sleep duration the orchestrator must wait.

### `skill_command_guard.py`
**Guarded tool:** `run_skill`
Blocks `run_skill` calls where `skill_command` does not start with `/`.
Catches the case where the orchestrator passed prose instead of a slash
command.

### `skill_cmd_guard.py`
**Guarded tool:** `run_skill`
Validates that path-argument skills (`implement-worktree-no-merge`,
`resolve-failures`, etc.) receive the file path as the first token rather
than buried after descriptive text.

### `remove_clone_guard.py`
**Guarded tool:** `remove_clone`
Denies `remove_clone` unless `keep="true"` is set explicitly. Prevents
unintended deletion of clones that may still have unpushed work.

### `open_kitchen_guard.py`
**Guarded tool:** `open_kitchen`
Blocks `open_kitchen` from running inside a headless session. Only human
operators may open the kitchen.

### `ask_user_question_guard.py`
**Guarded tool:** `AskUserQuestion`
Blocks `AskUserQuestion` in headless sessions unless a fresh kitchen-open
marker exists (TTL: 24 hours). Prevents leaf workers from attempting
interactive user prompts that can never be answered. Fails open on parse
errors or missing session ID. Session scope: headless only.

### `skill_orchestration_guard.py`
**Guarded tools:** `run_skill`, `run_cmd`, `run_python`
Blocks orchestration tools from skill-tier sessions. Enforces the tier
invariant: orchestrator and fleet sessions may call orchestration tools;
skill workers use native Claude Code tools only.

### `unsafe_install_guard.py`
**Guarded tool:** `run_cmd`
Denies `run_cmd` calls that perform editable installs without `--python
.venv`. Prevents pollution of the global Python environment.

### `github_mutation_guard.py`
**Guarded tools:** `Bash`, `run_cmd`
Denies raw pull-request review writes and any command containing multiple,
looped, dynamic, or otherwise unresolved GitHub mutations. Classification
resolves relative `gh api --input FILE` payloads against the command's own
*execution cwd* — a run_cmd tool call's own required target-directory
argument, or a Bash call's payload cwd — never against an unrelated session-
level field, and fails closed when no absolute execution cwd is available,
the request/`comments[]` count cannot be proven, or classification raises an
unexpected runtime error. Review publication must use
the typed `post_pr_review` tool; proven single non-review mutations retain
their existing policy. See "Fail Modes" below for the guard's exhaustive
deny-trigger vocabulary.

### `pr_create_guard.py`
**Guarded tool:** `run_cmd`
Blocks `gh pr create` called via `run_cmd` while the kitchen is open. Uses
`shlex.split` tokenisation to avoid false positives from quoted shell
arguments (e.g. `echo 'do not gh pr create'` does not match). Directs the
caller to use the `prepare_pr → compose_pr` pipeline instead.

### `git_ops_guard.py`
**Guarded tool:** `Bash`, `run_cmd`
Blocks destructive raw git CLI operations in headless skill sessions:
`commit --amend`, `push --force` / `-f` / `--force-with-lease`, `reset --hard`,
`clean -f` / `-fd`, and `checkout .` / `checkout -- .`. Uses `shlex.split`
tokenisation with global-flag skipping so `git -C /path commit --amend` and
full-path invocations like `/usr/bin/git push --force` are correctly detected.
Also catches interpreter-wrapped (`python3 -c "subprocess.run(['git', 'commit', '--amend'])"`)
and nested-shell forms. Session scope: headless only. Orchestrator sessions
are exempt. Per-subcommand allow-overrides are available via `git_ops_policy`
in `.hook_config.json` for future recipes that legitimately need these operations.

### `shell_capture_hook.py`
**Matched tool:** `Bash`
**Scope:** Codex sessions only (#4286 / ADR-0006 / ADR-0008); Claude Code is unaffected.
PreToolUse input-rewrite hook that wraps every native shell command on Codex in a
minimal isolated-Python runner. The runner opens the supplied `cwd` as a
`ProjectAnchor` directory descriptor before deriving a physical path, then opens or
creates `.autoskillit`, `temp`, and `shell_capture` relative to retained directory
descriptors without following symlinks. It reads output policy through the verified
`temp` descriptor and creates `shell_<uuid16>.log` exclusively with no-follow
semantics.

The child sends merged stdout+stderr through one pipe. The runner measures the byte
order observed from that pipe; it does not claim application-causal ordering for
concurrent writes. Actual EOF is the only completion boundary. Direct-shell exit,
`setsid()`, job detachment, silence, and elapsed time do not finalize while a
descendant retains a writer. A descendant that closes or redirects every inherited
writer does not delay capture. There is no capture-local deadline; an outer timeout
produces failure evidence.

The drain computes bytes, SHA-256, inline, head, and tail in one pass. After EOF the
runner closes its drain writer, preserves the raw exited-or-signaled outcome, verifies
the retained carrier descriptor, and syncs it before committing immutable FINAL.
Small captures replay only verified bytes and issue no token. Oversized captures emit
verified head and tail plus one canonical V2 marker containing an opaque published
reference or explicit unavailable state. V2 contains no path and no
`complete=true` authority.

The user-facing configuration uses `output_budget.guard_enabled` and
`output_budget.shell_max_inline_bytes`. The server serializes those values into the
stdlib hook bridge as `output_budget_policy.disabled` (the inverse of
`guard_enabled`) and `output_budget_policy.shell_max_inline_bytes`; the runner reads
that internal bridge shape after verified policy loading. No rendered-marker ceiling
is implemented.

#### Capture Artifact Lifecycle

| Phase | Behavior |
|-------|----------|
| Creation | Runner durably reserves a private staging name, creates it relative to the verified capture-directory fd, acquires a writer lease, and publishes the public name without replacement |
| FINAL | Only `verify_capture_snapshot()` can produce the value accepted by `commit_verified_snapshot()`; the completed carrier is synced first and later transitions preserve exact manifest bytes |
| Publication | Oversized FINAL issues one bearer token and stores only its bound hash; publication verifies that tuple and can return published or unavailable but cannot mint another token |
| Delivery | Checked write-all rejects invalid/no-progress writes and flushes hook stdout before `delivered`; this boundary does not prove host, UI, history, or model visibility |
| Reader | `open_verified_capture()` authenticates the token, takes a shared lease, revalidates the descriptor, and exposes exact bounded reads without a path, descriptor, or write API |
| Retention | References expire no later than the one-hour retention deadline; active producer-exclusive and reader-shared leases block cleanup |
| Ownership | The producer transfers the retained carrier while preserving exclusive ownership through initial publication and delivery |
| Cleanup — installed runner | Every valid run/reject invocation performs one bounded sweep after producer resources release |
| Cleanup — session lifecycle | `capture_lifecycle_hook.py` performs the same bounded sweep at `SessionStart` in both interactive and headless sessions |
| Naming contract | `shell_[0-9a-f]{16}.log` — files not matching this pattern are never deleted |
| Safety | Capture components reject symlinks; artifacts reject collisions, hardlinks, unsafe modes, and identity changes; a hostile same-UID process that ignores advisory locks is excluded |
| Failure mode | Pre-FINAL failure has no manifest/reference/success marker; post-FINAL failure preserves FINAL and changes only reference or delivery state; cleanup remains bounded and fail open |

The durable lifecycle is `RESERVED` → `STAGED` → `PUBLISHED_WRITING` →
`FINALIZED` or `FAILED`. An unlocked active record becomes `ABANDONED`.
Eligible terminal or abandoned records move through `DELETING` to `DELETED`,
or `TAMPERED`; operational failures preserve the current phase and reschedule
it with capped backoff. FINAL also carries independent reference
(`not_requested`, `issued`, `published`, `unavailable`, `unknown`, `expired`,
`revoked`) and
delivery (`not_attempted`, `attempting`, `delivered`, `failed`, `unknown`) states.
Lost pre-delivery tokens become unavailable; an interrupted attempting delivery
becomes unknown and is never re-emitted. Finalized and failed records use their
terminal transition as the retention clock; abandoned records use their durable
creation time.

Eligibility is not a wall-clock scheduler. Deletion occurs on the next enabled,
trusted installed runner-tail or cleanup-only `SessionStart` trigger. If hooks
are disabled, no trigger runs and eligible artifacts remain. Each trigger
bounds rows examined, monotonic duration, ledger bytes, and maintenance work.
Contended or failed rows are durably rescheduled with capped retry backoff so
one record cannot starve the backlog.

Only lifecycle-recorded `shell_[0-9a-f]{16}.log` artifacts with revalidated
project, capture-root, and inode identities enter quarantine deletion. Fresh
records, live writers, nonmatching names, symlinks, FIFOs, hardlinks,
world-writable files, identity replacements, unexpected link counts, and
tampered entries survive. `deleted_bytes` reports logical managed bytes
committed deleted exactly once; it is not evidence of physical block reclamation.

Ledger frames are bounded canonical JSON with revisions and checksums. Recovery
truncates only an incomplete final frame and rejects corrupt middle frames,
revision gaps, unknown versions, and conflicting manifests. The checksum is not an
authenticated head: clean suffix truncation, old-ledger replay, and a hostile
same-UID payload/checksum rewrite remain outside detection. Native local Linux
`fcntl.flock` behavior is the tested cooperative lease boundary; network filesystems
and processes that ignore advisory locks are excluded. Ordered sync supports
process-termination recovery, not universal OS-crash or power-loss durability.

Codex hook generation includes the cleanup-only SessionStart owner and excludes the
separate interactive-only resume reminder. Runner-tail cleanup is the
authoritative interactive/headless Bash owner; cleanup-only `SessionStart` is
the supplemental startup owner. ADR-0008 resolves #4322 for Codex shell capture
only. Trap isolation (#4323), a rendered ceiling (#4324), public bounded retrieval
(#4325), broader private-publication policy (#4326), partial/quota accounting
(#4327), upstream live visibility (#4329), and general producer adoption (#4335)
remain downstream work.

#### Cleanup diagnostic severity

Both cleanup owners (runner-tail and SessionStart) route their outcome through
one classifier, `classify_cleanup_outcome(progress, blocker, errors)`
(`hooks/_capture/_types.py`), before emission:

| Severity | Trigger | Emission |
|---|---|---|
| `healthy` | no blocker (`NONE`), or the store doesn't exist yet (`STORE_ABSENT`) | none |
| `deferred` | a bounded work budget (records/attempts/transitions/cursor-writes/replay-bytes/duration) was exhausted, but this pass still made progress | none — a bounded backlog is not, by itself, attention-grade |
| `stalled` | externally blocked (lock contention, an in-flight migration, filesystem authority/permission/IO/ledger failure), or a budget blocker with *zero* progress this pass | one neutral line naming the blocker |
| `failed` | `errors > 0` | one failure-worded line — the only severity whose rendered text may contain "failed" |

`errors` always wins regardless of blocker or progress. An externally-blocked
store (e.g. a held carrier lease blocking migration) stays `stalled` on every
pass, independent of progress, so it never goes silent the way a merely
budget-bounded backlog does. Every non-`healthy`, non-`deferred` message is
rendered through `hooks/_policy_event.py`'s `PolicyEvent` +
`render_provenance_prefix` — no hook constructs its own `[AutoSkillit ...]`
literal (`tests/arch/test_hook_message_provenance.py`).

#### Declared native-shell control mode

`shell_capture_hook.py _resolve_control` reads `AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE`
plus the four managed-identity vars and resolves to one of three outcomes, in
addition to the fully-managed `direct` path used by managed headless/skill/
resume/food-truck sessions:

| State | Resolution | Diagnostic |
|---|---|---|
| `capture` declared, no managed identity | capture mode | none — this is cook's normal, declared state |
| mode unset entirely (nothing declared) | capture mode | one neutral note: "native-shell control undeclared; using capture" |
| declared but incomplete/invalid managed identity | capture mode | one neutral note: "incomplete managed native-shell controls; falling back to capture" |

Codex cook sessions (`codex.py build_interactive_cmd`) positively declare
`AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE=capture` — injected after
`CodexEnvPolicy().build_env()` returns, since extras-side injection is
unconditionally stripped by the managed native-shell env filter. Cook remains
structurally unmanaged (no managed-identity params), now *declaredly* so:
absence of the declaration is a genuine anomaly again, not the common case.

#### Directory-reconciliation scan phase

Every prior cleanup path only ever acts on records the ledger already knows
about — a `shell_[0-9a-f]{16}.log` file written before a crash, a ledger
reset, or a legacy pre-ledger run has no record and was permanently
invisible to cleanup. `hooks/_capture/_orphan_scan.py` (stdlib-only) closes
that gap. `SweepBudgetSpec` gains `max_directory_entries_scanned`
(`_types.py`) — 0, the `RUNNER_TAIL_BUDGET` default, disables the phase
entirely, so per-command runner-tail latency is unaffected; `SESSION_START_BUDGET`
sets it to 512. The scan runs inside `run_bounded_sweep` after record-sweep
work, only while duration budget remains.

Directory entries are visited in sorted-name order — the only stable,
restartable position `os.scandir` supports — resumed via a persisted
`.orphan-scan-cursor` sidecar written through the same descriptor-relative
atomic helper, `_control_file.publish_private_file()`, that
`.capture-sweep-cursor` uses (never `.write_text()`), so repeated
budget-bounded invocations cover the whole directory without rescanning
from zero.

A candidate name is *adopted*, never deleted directly, only when every gate
holds jointly:

| Gate | Rule |
|---|---|
| Name pattern | matches `^shell_[0-9a-f]{16}\.log$` exactly |
| No symlink traversal | `lstat` shows a regular file — never `Path.is_file()`, which follows symlinks; a symlinked capture root once let cleanup escape the project (#4319) |
| Age | mtime at least 24h old — comfortably beyond the one-hour finalize/abandon eligibility grace, so a file mid-write is never a candidate |
| Not tracked | name is not the public name of any non-`DELETED`-phase ledger record — a `DELETING`-phase record's file, still on disk mid-quarantine, is excluded, or adoption would create a duplicate record for a tracked name; mtime alone is not a liveness signal, so this gate and the age gate above are both required jointly (#4321) |

Adoption re-verifies every gate again under lock — the scan above runs
unlocked — and constructs a `CaptureStatus.LEGACY_CLEANUP_ONLY` record, the
same shape the legacy-ledger decode path produces, with
`retention_phase=ELIGIBLE`: immediately due for the existing two-phase
quarantine deletion. Admission is capacity-gated exactly like a real
`reserve_capture()` call and shares the sweep's `max_transitions` budget — a
capacity-exhausted candidate is silently skipped, deferred to a later
invocation once cleanup frees room, so orphan adoption can only ever compete
for the same active-record ceiling real captures do, never bypass or starve
it (the same class of self-starvation issue #4440 fixed for the
record-sweep path).

Codex hook generation includes the scan-enabled `SESSION_START_BUDGET` path:
`capture_lifecycle_hook.py` is registered `codex_status="works-as-is"`,
`session_scope="any"` — the exclusion issue #4320 fixed no longer applies.

#### Bounded lock-contention retry

A single non-blocking `flock()` attempt used to abort a sweep immediately on
any contention, including the 256-attempt `SESSION_START_BUDGET` pass —
`session_scope="any"` means every concurrent session contends the same lock
at startup. `CaptureLifecycleStore._locked(blocking=False)` now retries on
`EAGAIN`/`EWOULDBLOCK` with jittered, doubling backoff (5–20ms base, capped,
jitter from the stdlib `random` module's OS-entropy-seeded per-process
state, never a wall-clock-derived source) bounded by the sweep's own
`max_duration_seconds` — no new configuration knob. `RUNNER_TAIL_BUDGET`
(50ms) naturally permits one or two retries; `SESSION_START_BUDGET` (1.0s)
rides out startup stampedes. `LOCK_CONTENDED` is only ever returned once the
entire budget has elapsed without acquisition; every other blocking caller
(every non-sweep transition — `reserve_capture`, `commit_verified_snapshot`,
`get_record`, ...) is unaffected and keeps today's kernel-blocking wait.

Store-open lock acquisition is bounded the same way. Opening a store runs
`_normalize_interrupted_deliveries` — its own `_locked()` calls — before a
sweep is ever reached, so a contention there was previously unbounded
regardless of how tight the caller's budget was: the budget only started
governing retries once `.sweep()` began. `open_capture_lifecycle` and
`CaptureLifecycleStore.from_open_authorities` accept an `open_budget`
parameter; when supplied, it primes the retry mechanism (the same
`_sweep_budget`/deadline fields the sweep body reads) before
`_normalize_interrupted_deliveries` runs, and `normalize_interrupted_deliveries`
switches its lock acquisitions to the bounded non-blocking path for exactly
that window. `reconcile_capture_store` passes its own `budget` as
`open_budget`, so both `RUNNER_TAIL_BUDGET` and `SESSION_START_BUDGET` bound
the whole reconciliation operation, not merely the sweep body; a
`LockContended` that exhausts the budget during store-open surfaces as the
same `LOCK_CONTENDED` outcome the sweep body reports.
`capture_store_stats()` opens with `RUNNER_TAIL_BUDGET` as its own
`open_budget` for the same reason — a diagnostic read must never hang.
Every other caller (`create_artifact`, direct construction in tests, ...)
passes nothing and keeps today's blocking-until-acquired open unchanged.

#### Stats and reclamation CLI

`hooks._capture._reconcile.capture_store_stats()` is the single read-only
adapter both the doctor battery's capture-store check and `autoskillit
capture-store` (without `--reclaim`) call, so neither surface can drift from
what a real reconciliation pass would find. `autoskillit capture-store
--reclaim` loops `reconcile_capture_store` with a generous one-time
`RECLAIM_BUDGET` until a clean pass — no due records, no adoptable orphans —
or a hard iteration cap. It exists for bulk pre-existing backlog; the
SessionStart scan phase above keeps new debris from ever accumulating again.

### `generated_file_write_guard.py`
**Guarded tools:** `Write`, `Edit`
Denies writes to generated files (`hooks.json`, `settings.json`). The hooks
file must be regenerated through `generate_hooks_json()`, never edited by
hand.

### `recipe_write_advisor.py`
**Matched tools:** `Write`, `Edit`
Non-blocking advisory: suggests `/autoskillit:write-recipe` or
`/autoskillit:make-campaign` when writing recipe YAML files in
`.autoskillit/recipes/` or `src/autoskillit/recipes/`. Silently skips
headless sessions to avoid noise in automated runs. Never blocks tool
execution. Session scope: interactive only.

### `grep_pattern_lint_guard.py`
**Guarded tool:** `Grep`
Denies `Grep` calls that contain `\|` (POSIX BRE alternation) in the
pattern. The Grep tool wraps ripgrep, which uses ERE/PCRE syntax where bare
`|` is alternation; `\|` matches a literal backslash-pipe, causing silent
zero-result failures. Returns the corrected ERE pattern (replacing `\|` with
`|`) in the deny message.

### `mcp_health_advisor.py`
**Matched tools:** `Bash`, `Write`, `Edit`, `Read`, `Glob`, `Grep`
Detects MCP server disconnection by reading `active_kitchens.json` and checking
PID liveness. Injects informational message suggesting `/MCP` reconnection when
all registered server PIDs for the project are dead. Never blocks tool execution.
Interactive sessions only.

### `fleet_dispatch_guard.py`
**Guarded tool:** `dispatch_food_truck`
Blocks `dispatch_food_truck` from headless callers. Prevents recursive
L3→L3 fleet session creation where a headless session launches another fleet
of headless sessions. Fails open on malformed input. Session scope: headless
calls are denied; interactive callers pass through.

### `review_loop_gate.py`
**Guarded tools:** `wait_for_ci`, `enqueue_pr`
Blocks these tools when `review_gate_state.json` has `gate == "LOOP_REQUIRED"`
and `check_review_loop` has not yet been called. Enforces the review-loop
invariant: after a `changes_requested` verdict the orchestrator must call
`run_python` with `callable='autoskillit.smoke_utils.check_review_loop'`
before proceeding to CI/merge steps.

### `pipeline_step_guard.py`
**Guarded tool:** `run_skill`
Non-blocking advisory: emits `additionalContext` warning when a step has unmet
dependencies. Permission is always `allow` — the server-side `_check_pipeline_deps`
in `run_skill` is the primary enforcer. Fails open on missing tracker or
malformed input.

### `fabricated_completion_guard.py`
**Guarded tools:** all tools in top-level orchestrator sessions
Denies the next tool call when the newest logical parent-assistant turn contains a
complete `<bg_result>` or terminal task-notification and a fresh
`run-skill-in-progress` marker binds it to the exact hook session. The element may be
embedded in prose, quoted, wrapped, or code-fenced. Claude physical records coalesce
only when their exact tagged identity matches: the full `requestId`/`message.id` pair,
`requestId` alone, or `message.id` alone. Identity shapes never merge with each other.

The bounded fragment grammar accepts `bg_result`, `task-notification`, and
`task_notification`, simple attributes, a non-empty body, and an exactly matching
closer. Task status is inspected only inside that element and must be `completed`,
`failed`, or `cancelled`. Mismatched, nested, empty, incomplete, and nonterminal
fragments fail open. Transcript paths come only from the trusted hook payload;
malformed records, later logical-turn boundaries, subagents, wrong sessions, and
missing or stale exact-session markers also fail open. Interactive orders and headless
orchestrators receive the same protection.
This hook is defense in depth: the server's one-shot `run_skill` receipt and
`complete_run_skill_result` acknowledgement are the completion authority. After transport
loss, `recover_run_skill_result` may rebind the sole delivered receipt once to the current
request session in the same kitchen; ambiguous or previously recovered receipts are refused.

### `exploration_request_identity_guard.py`
**Guarded tools:** `enable_exploration`, `submit_exploration_query`,
`get_exploration_page`, `resume_exploration_context`

For interactive Claude requests, copies the complete `tool_input` mapping and
overwrites only the internal exploration request token. The token addresses a
short-lived, tool-bound record containing the current hook event's native
`session_id`; `agent_id` is never used as the lease key. Supported events fail
closed when their identity is malformed or the record cannot be written, while
malformed JSON and unrelated tools remain fail-open. Codex and headless terminal
authority do not use this bridge.

## PostToolUse hooks (9)

### `pretty_output_hook.py`
**Guarded tools:** all AutoSkillit MCP tools
Reformats raw JSON responses into Markdown key-value pairs for readable
display and reduced token usage.

### `token_summary_hook.py`
**Guarded tool:** `run_skill`
After `run_skill` returns a GitHub PR URL, appends a `## Token Usage Summary`
table to the PR body so reviewers can see per-step token cost.

### `quota_post_hook.py`
**Guarded tool:** `run_skill`
After `run_skill` returns, replaces the display output with a quota warning when
the cached binding window marks `should_block=True` (per-window threshold —
see `quota_guard.py` above), no explicit `AUTOSKILLIT_QUOTA_GUARD__DISABLED`
override is set, and no per-session disable marker exists. The replacement
output is generated by the hook and does not echo raw `tool_response` content.

### `quota_guard_state_post_hook.py`
**Guarded tools:** `disable_quota_guard`, `close_kitchen`
Writes or clears the per-session quota-disable marker file in `kitchen_state/`.
After a successful `disable_quota_guard` response, the hook writes
`<session_id>_quota_guard_disabled.json` so the PreToolUse and PostToolUse
quota hooks bypass enforcement for that exact session only. After a
successful `close_kitchen` response, the hook clears only that session's
marker. The marker is read by `quota_guard.py` and `quota_post_hook.py` via
the shared helper in `_hook_settings.py`. If atomic marker write fails, the
hook surfaces an `updatedMCPToolOutput` rewrite so a disable response cannot
appear successful when no marker was written.

### `review_gate_post_hook.py`
**Guarded tools:** `run_skill`, `run_python`
Writes, updates, or clears `review_gate_state.json` in response to gate
sentinel tags in `run_skill` output: `%%REVIEW_GATE::LOOP_REQUIRED%%` sets
the gate and records the PR number; `%%REVIEW_GATE::CLEAR%%` removes the
state file. When `run_python` calls `check_review_loop`, marks
`check_review_loop_called: True` in the state so `review_loop_gate.py` will
unblock `wait_for_ci`/`enqueue_pr`.

### ~~`pipeline_step_post_hook.py`~~ (RETIRED)
Step completion marking is now **server-authoritative**: the `run_skill`
handler in `tools_execution.py` writes step completion at the adjudication
point, using the same tracker resolver as the dependency enforcer. This
eliminates the split-brain between client-side hook writes and server-side
enforcement reads that caused false `DEPENDENCY UNMET` denials (#4293).

### `recipe_confirmed_post_hook.py`
**Guarded tool:** `run_skill`
Writes a `{session_id}_recipe_confirmed.json` marker to `kitchen_state/`
after the first successful `run_skill` completes. This marker is read by
`open_kitchen_guard.py` to block mid-run recipe reloads. Idempotent —
skips writing if the marker already exists. Fails open on all error paths.

## SessionStart hooks (2)

### `capture_lifecycle_hook.py`
Runs one bounded, cleanup-only shell-capture lifecycle sweep using the absolute
`cwd` from the hook payload. It has no headless early exit, emits no stdout,
and always exits zero. Cleanup errors are bounded and fail open independently
of reminder delivery.

### `session_start_hook.py`
Injects a reminder to call `/autoskillit:open-kitchen` when resuming a
prior session (transcript_path size > 0). Without this, resumed orchestrator
sessions silently lose access to the kitchen tools.

## Fail Modes

All guard scripts fail-**open** for malformed or unparseable input: a JSON decode
failure produces exit 0 (approve). This prevents a broken hook from blocking the
entire tool chain.

Six guards additionally fail-**closed** for valid input with unrecognized values,
as a defense-in-depth measure against privilege escalation:

| Guard | Fail-closed condition | Rationale |
|-------|----------------------|-----------|
| `skill_command_guard.py` | Unexpected runtime error (not JSON parse) | Unknown failure mode = deny rather than risk executing an unvalidated command |
| `open_kitchen_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not gain kitchen access |
| `skill_orchestration_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not call orchestration tools (`run_skill`, `run_cmd`, `run_python`) |
| `background_exec_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not bypass `run_in_background` prohibition |
| `github_mutation_guard.py` | Ambiguous or unresolved GitHub mutation command, or unexpected classifier error | Unknown mutation scope must not bypass the structured review publisher |
| `exploration_request_identity_guard.py` | A supported exploration event lacks bounded native identity or its one-shot record cannot be written | A Claude exploration call must not execute without request-correlated authority |

**Design principle:** Garbage-in (malformed hook input) = fail-open. Unknown-tier
(valid input, unrecognized value) = fail-closed.

All remaining guards (`fleet_dispatch_guard.py`, `quota_guard.py`,
`mcp_health_advisor.py`, `branch_protection_guard.py`, etc.) fail-open in every
failure scenario — malformed input, unrecognized session types, runtime errors,
and missing data.

### `github_mutation_guard.py` execution-cwd semantics and trigger vocabulary

The guard extracts two independent facts from each payload via the shared
`hooks/_hook_payload.py` module: the *payload cwd* (the session-level `cwd`
field) and the *execution cwd* (a run_cmd tool call's own `cwd` argument, or
a Bash call's payload cwd — the directory the command actually runs in).
These are never compared against each other; a run_cmd call's target
directory differing from the session cwd is the normal shape of every
worktree-topology call, not a conflict. Mutation classification resolves
relative `--input` files against the execution cwd only; a missing or
relative execution cwd degrades cleanly to "unresolved" rather than denying
every command that carries one.

Every deny routes through one of five machine-readable triggers
(`DenyTrigger` in `github_mutation_guard.py`), each with its own reason text:

| Trigger | Meaning |
|---------|---------|
| `field_confusion` | The payload carries both the Bash and run_cmd command fields, or a stray `cwd` inside a Bash `tool_input` — which text executes is ambiguous. |
| `malformed_command` | The command field is missing or not a string. |
| `unresolved_mutation` | Mutation cardinality or target cannot be statically proven safe (dynamic values, unresolved `--input`, repeatable/dispatch constructs reaching a possible GitHub exec). |
| `multiple_mutations` | The command issues more than one GitHub mutation request. |
| `review_mutation` | The command is a raw pull-request review publication. |

## Drift detection

`cli/_doctor.py:_check_hook_registry_drift` calls `generate_hooks_json()` and
compares against the deployed `hooks.json` field by field, reporting any
missing or orphaned hook scripts. The check is gated by a 12-hour dismissal
cooldown to keep the doctor noise level reasonable; missing hook files are
detected separately by `_check_hook_health` so an ENOENT does not collapse
into a generic drift report.

## Stdlib-only rationale

Every hook script imports only the Python standard library. The hooks run
before any project virtualenv is activated, and Claude Code spawns them as
plain `python` subprocesses, so any third-party import would fail in the
common case where the user has not installed AutoSkillit's dependencies into
the global Python.

## Safety configuration

```yaml
# .autoskillit/config.yaml
safety:
  protected_branches: ["main", "develop", "stable"]
  require_dry_walkthrough: true
  test_gate_on_merge: true
  reset_guard_marker: ".autoskillit-workspace"

quota_guard:
  enabled: true
  short_window_threshold: 85.0
  long_window_threshold: 95.0
  long_window_patterns: ["weekly", "sonnet", "opus"]
  buffer_seconds: 60
```

See **[Configuration](../configuration.md)** for all safety-related settings.
