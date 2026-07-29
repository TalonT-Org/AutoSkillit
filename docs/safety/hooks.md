# Hooks

AutoSkillit registers 43 Claude Code hook scripts: 32 PreToolUse, 9 PostToolUse,
and 2 SessionStart. Every script is stdlib-only Python so it can run before the
project virtualenv is on the path. Scripts live in `src/autoskillit/hooks/`
and are bound to event types in `src/autoskillit/hook_registry.py` via the
`HOOK_REGISTRY` list of `HookDef` entries; `generate_hooks_json()` then
materializes the canonical `hooks.json` that Claude Code reads.

## PreToolUse hooks (32)

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

Three guards additionally fail-**closed** for valid input with unrecognized values,
as a defense-in-depth measure against privilege escalation:

| Guard | Fail-closed condition | Rationale |
|-------|----------------------|-----------|
| `skill_command_guard.py` | Unexpected runtime error (not JSON parse) | Unknown failure mode = deny rather than risk executing an unvalidated command |
| `open_kitchen_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not gain kitchen access |
| `skill_orchestration_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not call orchestration tools (`run_skill`, `run_cmd`, `run_python`) |

**Design principle:** Garbage-in (malformed hook input) = fail-open. Unknown-tier
(valid input, unrecognized value) = fail-closed.

All remaining guards (`fleet_dispatch_guard.py`, `quota_guard.py`,
`mcp_health_advisor.py`, `branch_protection_guard.py`, etc.) fail-open in every
failure scenario — malformed input, unrecognized session types, runtime errors,
and missing data.

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
