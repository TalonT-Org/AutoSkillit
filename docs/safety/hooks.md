# Hooks

AutoSkillit registers 14 Claude Code hook scripts: 10 PreToolUse, 3 PostToolUse,
and 1 SessionStart. Every script is stdlib-only Python so it can run before the
project virtualenv is on the path. Scripts live in `src/autoskillit/hooks/`
and are bound to event types in `src/autoskillit/hook_registry.py` via the
`HOOK_REGISTRY` list of `HookDef` entries; `generate_hooks_json()` then
materializes the canonical `hooks.json` that Claude Code reads.

## PreToolUse hooks (10)

### `branch_protection_guard.py`
**Guarded tools:** `merge_worktree`, `push_to_remote`
Denies merges and pushes targeting branches in `safety.protected_branches`
(`main`, `integration`, `stable` by default). Pure-function check via
`core/branch_guard.is_protected_branch`.

### `quota_check.py`
**Guarded tool:** `run_skill`
Blocks launching new headless sessions when the cached binding window marks
`should_block=True`. The threshold is per-window: short windows (e.g.
`five_hour`) use `quota_guard.short_window_threshold` (default 85.0%); long
windows matched by `quota_guard.long_window_patterns` (default `weekly`,
`sonnet`, `opus`) use `quota_guard.long_window_threshold` (default 98.0%).
Reports the exact sleep duration the orchestrator must wait.

### `skill_command_guard.py`
**Guarded tool:** `run_skill`
Blocks `run_skill` calls where `skill_command` does not start with `/`.
Catches the case where the orchestrator passed prose instead of a slash
command.

### `skill_cmd_check.py`
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

### `leaf_orchestration_guard.py`
**Guarded tools:** `run_skill`, `run_cmd`, `run_python`
Blocks orchestration tools from leaf-tier sessions. Enforces the tier
invariant: orchestrator and franchise sessions may call orchestration tools;
leaf workers use native Claude Code tools only.

### `unsafe_install_guard.py`
**Guarded tool:** `run_cmd`
Denies `run_cmd` calls that perform editable installs without `--python
.venv`. Prevents pollution of the global Python environment.

### `generated_file_write_guard.py`
**Guarded tools:** `Write`, `Edit`
Denies writes to generated files (`hooks.json`, `settings.json`). The hooks
file must be regenerated through `generate_hooks_json()`, never edited by
hand.

### `mcp_health_guard.py`
**Matched tools:** `Bash`, `Write`, `Edit`, `Read`, `Glob`, `Grep`
Detects MCP server disconnection by reading `active_kitchens.json` and checking
PID liveness. Injects informational message suggesting `/MCP` reconnection when
all registered server PIDs for the project are dead. Never blocks tool execution.
Interactive sessions only.

## PostToolUse hooks (3)

### `pretty_output.py`
**Guarded tools:** all AutoSkillit MCP tools
Reformats raw JSON responses into Markdown key-value pairs for readable
display and reduced token usage.

### `token_summary_appender.py`
**Guarded tool:** `run_skill`
After `run_skill` returns a GitHub PR URL, appends a `## Token Usage Summary`
table to the PR body so reviewers can see per-step token cost.

### `quota_post_check.py`
**Guarded tool:** `run_skill`
After `run_skill` returns, appends a quota warning to the tool output when
the cached binding window marks `should_block=True` (per-window threshold —
see `quota_check.py` above). Gives the orchestrator a chance to back off
voluntarily before the PreToolUse hook starts denying.

## SessionStart hook (1)

### `session_start_reminder.py`
Injects a reminder to call `/autoskillit:open-kitchen` when resuming a
prior session (transcript_path size > 0). Without this, resumed orchestrator
sessions silently lose access to the kitchen tools.

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
  protected_branches: ["main", "integration", "stable"]
  require_dry_walkthrough: true
  test_gate_on_merge: true
  reset_guard_marker: ".autoskillit-workspace"

quota_guard:
  enabled: true
  short_window_threshold: 85.0
  long_window_threshold: 98.0
  long_window_patterns: ["weekly", "sonnet", "opus"]
  buffer_seconds: 60
```

See **[Configuration](../configuration.md)** for all safety-related settings.
