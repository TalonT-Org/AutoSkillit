# Configuration Reference

## Quick Start

Most projects only need one setting — the test command:

    cd your-project
    autoskillit init

This creates `.autoskillit/config.yaml` with the test command you provide.
Everything else has sensible defaults. See [Getting Started](getting-started.md)
for a full tutorial.

## Common Configurations

### Minimal (just a test command)

```yaml
# .autoskillit/config.yaml
test_check:
  command: ["pytest", "-v", "--tb=short"]
```

### Full config with GitHub integration

```yaml
# .autoskillit/config.yaml
test_check:
  command: ["task", "test-check"]

model:
  default: "claude-sonnet-4-6"

worktree_setup:
  command: ["task", "install-worktree"]

branching:
  default_base_branch: main

github:
  default_repo: "my-org/my-repo"
```

### Project without `task` runner

```yaml
# .autoskillit/config.yaml
test_check:
  command: ["pytest", "-v", "--tb=short"]
  timeout: 600

worktree_setup:
  command: ["pip", "install", "-e", ".[dev]"]
```

## Layered Resolution

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

Partial configs are fine. Unset fields keep their defaults. View the resolved config with [`autoskillit config show`](cli.md#autoskillit-config-show).

## Test Command

Used by `test_check` and `merge_worktree`. The only setting most projects need.

```yaml
test_check:
  command: ["task", "test-check"]
  timeout: 600
```

`autoskillit init` sets this for you. Default: `["task", "test-check"]`.

## Model Selection

Control which model `run_skill` uses for headless sessions.

```yaml
model:
  default: sonnet    # default model when step has no model field
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

GitHub features (PR creation, issue management, CI status) require the `gh` CLI to be installed and authenticated:

    gh auth login

The `github.default_repo` setting is used when a bare issue number (`#42`) is provided without a full URL:

```yaml
github:
  default_repo: "owner/repo"
```

If you need a GitHub token for the AutoSkillit API (separate from `gh` CLI auth), place it in `.autoskillit/.secrets.yaml` (never commit).

## Branching

```yaml
branching:
  default_base_branch: main   # default base branch for recipes
```

Default: `main`. Override if your project uses a different integration branch (e.g. `integration` or `develop`):

```yaml
branching:
  default_base_branch: integration
```

## Session Diagnostics (Linux)

```yaml
linux_tracing:
  enabled: true           # default: true
  proc_interval: 5.0      # seconds between snapshots
  log_dir: ""             # empty = platform default
  tmpfs_path: "/dev/shm"  # RAM-backed path for crash resilience
```

See [Session Diagnostics](developer/diagnostics.md) for details on log output.

## Headless Sessions

```yaml
run_skill:
  timeout: 7200            # 2-hour session timeout (seconds)
  stale_threshold: 1200    # 20 minutes of no output before declaring stale
```

## MCP Response Tracking

```yaml
mcp_response:
  alert_threshold_tokens: 2000   # warn when tool responses exceed this token estimate
```

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

## Token Usage

Controls how token consumption is reported per pipeline step.

```yaml
token_usage:
  verbosity: "summary"   # "summary" | "full" | "none"
```

- `"summary"` — report total tokens per step (default)
- `"full"` — report input and output tokens separately per step
- `"none"` — disable token reporting

## Migration

```yaml
migration:
  suppressed:
    - "some-migration-id"
```

`suppressed` is a list of migration IDs to skip. Useful when a migration is not applicable to a specific project (e.g. you never used the feature the migration addresses). Migration IDs appear in warning output from `autoskillit migrate`.

## Logging

```yaml
logging:
  level: "INFO"         # "DEBUG" | "INFO" | "WARNING" | "ERROR"
  json_output: null     # true = JSON lines, false = human-readable, null = auto (stderr tty detection)
```

Controls the autoskillit server logger. Useful for debugging:

```bash
AUTOSKILLIT_LOGGING__LEVEL=DEBUG autoskillit serve
```

## Read DB

```yaml
read_db:
  timeout: 30       # SQLite connection timeout in seconds
  max_rows: 10000   # maximum rows returned per query
```

## Report Bug

```yaml
report_bug:
  output_dir: null  # null = {cwd}/.autoskillit/temp/bug-reports/
  timeout: 600
  github_filing: true
  github_labels: ["autoreported", "bug"]
```

## Environment Variables

### `AUTOSKILLIT_HEADLESS`

When set to `"1"`, marks the MCP server instance as a headless session. This activates several session-scoped behaviors:

- **Headless-tool reveal**: only `test_check` (headless-tagged) is revealed at startup.
  Kitchen-only tools (`run_skill`, `run_cmd`, `run_python`, `merge_worktree`, etc.) remain
  hidden — headless sessions do not orchestrate sub-sessions.
- **Orchestration blocked**: `run_skill`, `run_cmd`, and `run_python` are denied (headless sessions execute tasks, they do not orchestrate sub-sessions)
- **`open_kitchen` blocked**: cannot be triggered from within a headless session
- **Env isolation**: server-private env vars are stripped from any subprocess env passed to test runners, preventing leakage into user code

This is automatically set by `autoskillit order`, `cook`, and when launching sub-recipe headless sessions. Do **not** set this manually in user-facing orchestration sessions — it disables the protection that prevents the orchestrator from accidentally calling gated pipeline tools outside of a pipeline context.

## Skill Visibility

Controls which skill tiers are visible in each session mode.

```yaml
# .autoskillit/config.yaml
skills:
  tier1:   # Visible in plain $ claude sessions (plugin-scanned)
    - open-kitchen
    - close-kitchen
  tier2:   # Visible in cook and headless sessions (interactive)
    - investigate
    - make-plan
    # ...full list from defaults.yaml...
  tier3:   # Visible in cook and headless sessions (automation/pipeline)
    - open-pr
    - merge-pr
    # ...
```

Any bundled skill can be promoted or demoted by adding it to the desired tier list. A skill
in multiple tiers simultaneously is a validation error. See **[Skill Visibility](skills/visibility.md)**
for the full tier breakdown, session mode table, and override rules.

## Subset Categories

Disables functional groups of tools and skills together.

```yaml
# .autoskillit/config.yaml
subsets:
  disabled:
    - github     # hides all github-tagged tools and skills
    - ci         # hides CI-polling tools and skills
  custom_tags:
    my-favorites:
      - investigate
      - make-plan
```

Disabling a subset hides its members from all session modes — even after `open_kitchen`.
See **[Subset Categories](skills/subsets.md)** for the complete category listing and
FastMCP mechanics.

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

## Merge Queue Configuration

AutoSkillit's `wait_for_merge_queue` tool and the queue mode in `merge-prs` work
with GitHub's merge queue feature. For best results with automation use cases:

### `min_entries_to_merge_wait_minutes` = 0

GitHub branch rulesets expose a `min_entries_to_merge_wait_minutes` setting that adds
latency before a queued PR is eligible to merge. For the `integration` branch (or any
branch where AutoSkillit manages the PR queue), set this to `0`.

**Why:** AutoSkillit enters PRs one at a time or in small batches. A non-zero wait
multiplier adds unnecessary latency per PR. Setting it to `0` lets PRs merge as soon
as their CI passes.

**Location:** GitHub → Repository Settings → Branches → Branch protection rules →
select the integration ruleset → Merge queue → "Minimum entries to merge — wait X minutes".
Set to `0`.
