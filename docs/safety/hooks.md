# Hooks

AutoSkillit registers 24 Claude Code hook scripts: 18 PreToolUse, 5 PostToolUse,
and 1 SessionStart. Every script is stdlib-only Python so it can run before the
project virtualenv is on the path. Scripts live in `src/autoskillit/hooks/`
and are bound to event types in `src/autoskillit/hook_registry.py` via the
`HOOK_REGISTRY` list of `HookDef` entries; `generate_hooks_json()` then
materializes the canonical `hooks.json` that Claude Code reads.

## PreToolUse hooks (18)

### `branch_protection_guard.py`
**Guarded tools:** `merge_worktree`, `push_to_remote`
Denies merges and pushes targeting branches in `safety.protected_branches`
(`main`, `develop`, `stable` by default). Pure-function check via
`core/branch_guard.is_protected_branch`.

### `quota_guard.py`
**Guarded tool:** `run_skill`
Blocks launching new headless sessions when the cached binding window marks
`should_block=True`. The threshold is per-window: short windows (e.g.
`five_hour`) use `quota_guard.short_window_threshold` (default 85.0%); long
windows matched by `quota_guard.long_window_patterns` (default `weekly`,
`sonnet`, `opus`) use `quota_guard.long_window_threshold` (default 95.0%).
Reports the exact sleep duration the orchestrator must wait.

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

## PostToolUse hooks (5)

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
After `run_skill` returns, appends a quota warning to the tool output when
the cached binding window marks `should_block=True` (per-window threshold —
see `quota_guard.py` above). Gives the orchestrator a chance to back off
voluntarily before the PreToolUse hook starts denying.

### `review_gate_post_hook.py`
**Guarded tools:** `run_skill`, `run_python`
Writes, updates, or clears `review_gate_state.json` in response to gate
sentinel tags in `run_skill` output: `%%REVIEW_GATE::LOOP_REQUIRED%%` sets
the gate and records the PR number; `%%REVIEW_GATE::CLEAR%%` removes the
state file. When `run_python` calls `check_review_loop`, marks
`check_review_loop_called: True` in the state so `review_loop_gate.py` will
unblock `wait_for_ci`/`enqueue_pr`.

### `pipeline_step_post_hook.py`
**Guarded tool:** `run_skill`
After a successful `run_skill`, auto-marks the step as `complete` in the
pipeline tracker file. Appends a progress banner via `updatedMCPToolOutput`.
Uses `AUTOSKILLIT_DISPATCH_ID` env fallback for `order_id` resolution to
handle fleet-dispatched pipelines. Fails open on missing tracker or errors.

## SessionStart hook (1)

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
