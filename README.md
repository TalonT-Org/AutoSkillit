# AutoSkillit

Claude Code plugin that orchestrates automated workflows using headless sessions. Provides 22 MCP tools (15 gated behind user-only MCP prompts, 7 ungated) and 19 bundled skills registered as `/autoskillit:*` slash commands.

## Install

```bash
uv pip install -e .
```

Requires Python 3.11+.

## Quick Start

### 1. Install the package

```bash
uv pip install -e /path/to/autoskillit
```

### 2. Register the plugin

```bash
autoskillit install              # persistent plugin (recommended)
```

This registers a local marketplace and installs the plugin via `claude plugin install`. The plugin loads automatically in every Claude Code session. After updating, re-run `autoskillit install` to refresh the cache.

> **Note:** Do **not** also run `claude mcp add autoskillit ...` — the plugin already registers the MCP server. Adding a standalone entry creates a duplicate server process.

For one-off sessions without persistent installation:

```bash
claude --plugin-dir $(python -c "import autoskillit; print(autoskillit.__path__[0])")
```

### 3. Configure for your project

```bash
cd your-project
autoskillit init                              # prompts for test command
autoskillit init --test-command "pytest -v"   # non-interactive
```

This creates `.autoskillit/config.yaml`. Use `--force` to overwrite an existing config.

### 4. Open the kitchen

15 tools are gated by default. Activate them by typing the open prompt shown by `autoskillit doctor` or in the MCP tool list. The prompt name depends on how the plugin was loaded:

- Plugin install: `/mcp__plugin_autoskillit_autoskillit__open_kitchen`
- `--plugin-dir`: `/mcp__autoskillit__open_kitchen`

This uses MCP prompts (user-only, model cannot invoke) and survives `--dangerously-skip-permissions`.

## Running Pipelines

### From the terminal

```bash
autoskillit cook <recipe-name>
```

Launches a Claude Code session to execute the named recipe from `.autoskillit/recipes/`. The recipe YAML is validated before launch.

### From within Claude Code

Load a recipe via the `load_recipe` MCP tool, then follow the YAML steps:

```
list_recipes()           -> JSON array of {name, description, summary}
load_recipe("impl")      -> raw YAML content for agent to interpret
```

Both tools are ungated — available without calling `open_kitchen`.

## MCP Tools

| Tool | Gated | Purpose |
|------|-------|---------|
| `run_cmd` | Yes | Execute shell commands with timeout |
| `run_python` | Yes | Call a Python function by dotted module path (in-process) |
| `run_skill` | Yes | Run Claude Code headless with a skill command (optional `model` param) |
| `run_skill_retry` | Yes | Run Claude Code headless with API call limit (optional `model` param) |
| `test_check` | Yes | Run test suite, returns unambiguous PASS/FAIL |
| `merge_worktree` | Yes | Merge worktree branch after programmatic test gate |
| `reset_test_dir` | Yes | Clear test directory (reset guard marker required) |
| `classify_fix` | Yes | Analyze diff to determine restart scope (full vs partial) |
| `reset_workspace` | Yes | Reset workspace directory, preserving configured paths |
| `read_db` | Yes | Run read-only SQL queries against SQLite databases |
| `migrate_recipe` | Yes | Apply versioned migration notes to a recipe YAML file |
| `clone_repo` | Yes | Clone a repo into an isolated run directory |
| `remove_clone` | Yes | Tear down a clone directory (never raises) |
| `push_to_remote` | Yes | Push from clone to remote without touching source |
| `fetch_github_issue` | Yes | Fetch a GitHub issue as Markdown |
| `check_quota` | No | Check 5-hour API quota utilization |
| `kitchen_status` | No | Return version health and gate status |
| `list_recipes` | No | List available recipes from project and bundled sources |
| `load_recipe` | No | Load a recipe by name as raw YAML |
| `validate_recipe` | No | Validate a recipe against the schema and semantic rules |
| `get_pipeline_report` | No | Retrieve accumulated pipeline failure audit log |
| `get_token_summary` | No | Retrieve per-step token usage totals |

## Skills

Bundled skills invoked as `/autoskillit:<name>`:

| Skill | Purpose |
|-------|---------|
| `investigate` | Deep investigation of errors or questions without making code changes |
| `rectify` | Follow-up investigation that designs architectural immunity rather than direct fixes |
| `make-plan` | Create implementation plans through deep codebase exploration with subagents |
| `make-groups` | Decompose a large plan or spec into sequenced implementation groups |
| `dry-walkthrough` | Validate a plan by tracing each change without implementing |
| `review-approach` | Research modern solutions for a plan via web-search subagents |
| `implement-worktree` | Implement a plan in an isolated git worktree with full test and rebase |
| `implement-worktree-no-merge` | Implement in a worktree without merging — leaves it intact for the orchestrator |
| `retry-worktree` | Continue implementation after context exhaustion in a prior session |
| `resolve-failures` | Fix test failures in a worktree without merging |
| `audit-impl` | Audit a completed implementation against its plan — returns GO or NO GO |
| `analyze-prs` | Analyze open PRs for merge order, file overlaps, and complexity |
| `merge-pr` | Merge a single PR into the integration branch |
| `audit-friction` | Scan Claude Code project logs for repeated failure patterns and stuck workflows |
| `pipeline-summary` | Create a GitHub issue and PR summarizing pipeline bugs and fixes |
| `write-recipe` | Generate YAML recipes for `.autoskillit/recipes/` interactively or from another skill |
| `migrate-recipes` | Apply versioned migration notes to a recipe YAML file |
| `mermaid` | Create and edit Mermaid diagrams in markdown files |
| `setup-project` | Explore a target project and generate tailored recipes and config |

Use `autoskillit skills list` to see all bundled skills.

## Configuration

Layered YAML resolution: package defaults < `~/.autoskillit/config.yaml` (user) < `.autoskillit/config.yaml` (project) < `.autoskillit/.secrets.yaml` < env vars (`AUTOSKILLIT_SECTION__KEY`). Partial configs are fine — unset fields keep defaults. View resolved config with `autoskillit config show`.

### Test Command

Used by `test_check` and `merge_worktree`. The only setting most projects need.

```yaml
test_check:
  command: ["task", "test-check"]
  timeout: 600
```

`autoskillit init` sets this for you. Default: `["task", "test-check"]`.

### Model Selection

Control which model `run_skill` and `run_skill_retry` use for headless sessions.

```yaml
model:
  default: null      # default model when step has no model field (null = CLI default)
  override: null     # force all sessions to use this model (overrides step YAML)
```

Recipe steps can also specify a `model` field per-step in their YAML definition.

### Worktree Setup

Command to run after creating a git worktree (e.g. to install dependencies).

```yaml
worktree_setup:
  command: ["task", "install-worktree"]
```

Default: `null` (disabled).

### classify_fix

```yaml
classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"
```

File prefixes that trigger `full_restart` instead of `partial_restart`. Default: `[]`.

### reset_workspace

```yaml
reset_workspace:
  command: ["task", "clean"]
  preserve_dirs: ["data", ".cache"]
```

Default: `command: null` (disabled), `preserve_dirs: []`.

### Quota Guard

```yaml
quota_guard:
  enabled: true
  threshold: 80.0          # block run_skill when 5-hour utilization exceeds this %
  buffer_seconds: 60       # extra buffer after quota reset before resuming
  cache_max_age: 60        # seconds before a live quota fetch is triggered
```

### GitHub Integration

```yaml
github:
  default_repo: "owner/repo"   # used when a bare issue number (#42) is provided
# token goes in .autoskillit/.secrets.yaml (never commit)
```

### Safety and Gates

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

### Full Example

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

## Recipes

Declarative YAML pipeline definitions stored in `.autoskillit/recipes/` that guide the orchestrating agent through multi-step processes.

### Bundled recipes

| Recipe | Description |
|--------|-------------|
| `implementation-pipeline` | Clone > plan > verify > implement > test > merge > push > cleanup (supports group decomposition) |
| `bugfix-loop` | Reset > test > investigate > rectify > implement > verify > audit > merge |
| `investigate-first` | Clone > investigate > rectify > dry-walkthrough > implement > test > merge > push > cleanup |
| `audit-and-fix` | Clone > audit > investigate > rectify > implement > test > merge > push > cleanup |
| `smoke-test` | Self-contained integration test exercising the full orchestration path |

```bash
autoskillit recipes list              # show available recipes
autoskillit recipes show bugfix-loop  # print YAML
```

Agents access recipes via MCP resource: `recipe://bugfix-loop`. Project recipes in `.autoskillit/recipes/` override bundled ones.

### Recipe schema

```yaml
name: implementation
description: Plan and implement a task end-to-end.
summary: make-plan > dry-walk > implement > test > merge

ingredients:
  task:
    description: What to implement
    required: true
  base_branch:
    description: Branch to merge into
    default: main

kitchen_rules:
  - "NEVER use native Claude Code tools from the orchestrator."

steps:
  plan:
    tool: run_skill
    with:
      skill_command: "/autoskillit:make-plan ${{ inputs.task }}"
      cwd: "."
    on_success: verify
    on_failure: escalate
  verify:
    tool: run_skill
    with:
      skill_command: "/autoskillit:dry-walkthrough ${{ inputs.plan_path }}"
      cwd: "."
    on_success: implement
    on_failure: escalate
  implement:
    tool: run_skill_retry
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Implementation complete."
  escalate:
    action: stop
    message: "Failed — human intervention needed."
```

`/autoskillit:setup-project` generates recipes tailored to your project. `/autoskillit:write-recipe` generates them interactively.

## Diagnostics

```bash
autoskillit doctor              # check for stale MCP servers, missing config, plugin metadata
autoskillit doctor --output-json  # structured JSON output
autoskillit migrate             # report outdated recipes and available migrations
autoskillit migrate --check     # exit 1 if any migrations pending (CI use)
autoskillit quota-status        # check 5-hour API quota utilization
```

## Safety

- **Tool gating**: 15 tools disabled by default, require user activation via MCP prompt
- **Reset guard**: Destructive operations require a marker file (`.autoskillit-workspace`). Create with `autoskillit workspace init <dir>`
- **Dry-walkthrough gate**: Plans must be verified before implementation skills run
- **Test gate**: Programmatic test validation before merge (no bypass parameter)
- **Quota guard**: PreToolUse hook blocks `run_skill` when 5-hour API utilization exceeds threshold
- **Read-only DB**: Triple-layered protection (regex, OS-level `mode=ro`, SQLite authorizer)
- **Process tree cleanup**: psutil-based cleanup of all subprocess descendants

## Development

```bash
uv sync --extra dev
pre-commit install
task test-all                        # run tests
pre-commit run --all-files           # format, lint, typecheck
```

## License

MIT
