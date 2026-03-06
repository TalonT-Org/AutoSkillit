# Configuration Reference

AutoSkillit uses layered YAML configuration. Resolution order (last wins):

1. Package defaults (`autoskillit/config/defaults.yaml`)
2. User config (`~/.autoskillit/config.yaml`)
3. Project config (`.autoskillit/config.yaml`)
4. Secrets (`.autoskillit/.secrets.yaml`, never commit)
5. Environment variables

AutoSkillit uses [dynaconf](https://www.dynaconf.com/) for configuration. Environment variables override all file-based config using the prefix `AUTOSKILLIT_` with double underscores (`__`) to denote nesting:

```bash
AUTOSKILLIT_TEST_CHECK__TIMEOUT=300           # sets test_check.timeout to 300
AUTOSKILLIT_MODEL__DEFAULT=claude-sonnet-4-6  # sets model.default
AUTOSKILLIT_QUOTA_GUARD__ENABLED=false        # disables quota guard
```

This is useful for CI pipelines or per-session overrides without touching config files.

Partial configs are fine. Unset fields keep their defaults. View the resolved config with `autoskillit config show`.

## Test Command

Used by `test_check` and `merge_worktree`. The only setting most projects need.

```yaml
test_check:
  command: ["task", "test-check"]
  timeout: 600
```

`autoskillit init` sets this for you. Default: `["task", "test-check"]`.

## Model Selection

Control which model `run_skill` and `run_skill_retry` use for headless sessions.

```yaml
model:
  default: null      # default model when step has no model field (null = CLI default)
  override: null     # force all sessions to use this model (overrides step YAML)
```

Recipe steps can also specify a `model` field per-step in their YAML definition.

## Worktree Setup

Command to run after creating a git worktree (e.g. to install dependencies).

```yaml
worktree_setup:
  command: ["task", "install-worktree"]
```

Default: `null` (disabled).

## classify_fix

```yaml
classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"
```

File prefixes that trigger `full_restart` instead of `partial_restart`. Default: `[]`.

## reset_workspace

```yaml
reset_workspace:
  command: ["task", "clean"]
  preserve_dirs: ["data", ".cache"]
```

Default: `command: null` (disabled), `preserve_dirs: []`.

## Quota Guard

```yaml
quota_guard:
  enabled: true
  threshold: 80.0          # block run_skill when 5-hour utilization exceeds this %
  buffer_seconds: 60       # extra buffer after quota reset before resuming
  cache_max_age: 60        # seconds before a live quota fetch is triggered
```

Check current quota: `autoskillit quota-status`.

## GitHub Integration

```yaml
github:
  default_repo: "owner/repo"   # used when a bare issue number (#42) is provided
```

Token goes in `.autoskillit/.secrets.yaml` (never commit).

## Safety and Gates

```yaml
implement_gate:
  marker: "Dry-walkthrough verified = TRUE"
  skill_names: ["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]

safety:
  reset_guard_marker: ".autoskillit-workspace"
  require_dry_walkthrough: true
  test_gate_on_merge: true
```

These defaults are usually fine. Override per-project if needed.

## Full Example

```yaml
test_check:
  command: ["pytest", "-v", "--tb=short"]

model:
  default: "claude-sonnet-4-6"

worktree_setup:
  command: ["task", "install-worktree"]

classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"

reset_workspace:
  command: ["task", "clean"]
  preserve_dirs: ["data", ".cache"]

github:
  default_repo: "my-org/my-repo"
```
