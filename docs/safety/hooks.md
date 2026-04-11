# Hooks & Safety

AutoSkillit registers Claude Code hooks that enforce safety boundaries. All hooks are stdlib-only Python scripts that run as PreToolUse or PostToolUse handlers.

## PreToolUse Hooks

### Protected Branch Guard
**Tools:** `merge_worktree`, `push_to_remote`
Blocks merging into or pushing to protected branches. Default protected list: `main`, `integration`, `stable`. Configure via `safety.protected_branches` in config.

### Quota Guard
**Tools:** `run_skill`
Blocks launching new headless sessions when API quota utilization exceeds the configured threshold (default: 90% of the 5-hour window). Reports exact sleep duration needed.

### Skill Command Format Guard
**Tools:** `run_skill`
Blocks `run_skill` calls where `skill_command` doesn't start with `/`. Prevents the orchestrator from accidentally passing prose as a skill invocation.

### Skill Path Argument Guard
**Tools:** `run_skill`
Validates that path-argument skills (`implement-worktree-no-merge`, `resolve-failures`, etc.) receive the file path as the first argument, not buried after descriptive text.

### Clone Removal Guard
**Tools:** `remove_clone`
Blocks clone deletion when the branch has unpushed commits. Prevents permanent loss of unsynced work.

### Open Kitchen Guard
**Tools:** `open_kitchen`
Blocks headless sessions from calling `open_kitchen`. Only human operators may open the kitchen.

### Headless Orchestration Guard
**Tools:** `run_skill`, `run_cmd`, `run_python`
Blocks headless sessions from calling orchestration tools. Enforces the two-tier invariant: only the Tier 1 orchestrator may spawn headless sessions.

## PostToolUse Hook

### Pretty Output
**Tools:** All AutoSkillit tools
Reformats raw JSON responses into Markdown key-value format for better readability and reduced token usage.

## Safety Configuration

```yaml
# .autoskillit/config.yaml
safety:
  protected_branches: ["main", "integration", "stable"]
  require_dry_walkthrough: true
  test_gate_on_merge: true
  reset_guard_marker: ".autoskillit-workspace"

quota_guard:
  enabled: true
  threshold: 90.0
  buffer_seconds: 60
```

See **[Configuration](configuration.md)** for all safety-related settings.
