# AutoSkillit

Claude Code plugin that orchestrates automated workflows using headless sessions. Provides 14 MCP tools (10 gated behind user-only MCP prompts, 4 ungated) and 13 bundled skills registered as `/autoskillit:*` slash commands.

## Install

```bash
pip install -e .
```

Requires Python 3.11+.

## Quick Start

### 1. Install the package

```bash
pip install -e /path/to/autoskillit
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

10 tools are gated by default. Activate them by typing the open prompt shown by `autoskillit doctor` or in the MCP tool list. The prompt name depends on how the plugin was loaded:

- Plugin install: `/mcp__plugin_autoskillit_autoskillit__open_kitchen`
- `--plugin-dir`: `/mcp__autoskillit__open_kitchen`

This uses MCP prompts (user-only, model cannot invoke) and survives `--dangerously-skip-permissions`.

## Running Pipelines

### From the terminal

```bash
autoskillit orchestrate <script-name>
```

Launches a constrained Claude Code session that runs the named pipeline script from `.autoskillit/scripts/`. The session is restricted to `AskUserQuestion` and AutoSkillit MCP tools only — no direct file system or shell access. The script YAML is validated before launch, and the orchestrator instructions are injected automatically.

Cannot be run inside an existing Claude Code session.

### From within Claude Code

Load a pipeline script via the `load_skill_script` MCP tool, then follow the YAML steps:

```
list_skill_scripts()          -> JSON array of {name, description, summary}
load_skill_script("impl")    -> raw YAML content for agent to interpret
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
| `reset_test_dir` | Yes | Clear test directory (reset guard marker) |
| `classify_fix` | Yes | Analyze diff to determine restart scope (full vs partial) |
| `reset_workspace` | Yes | Reset workspace directory, preserving configured paths |
| `read_db` | Yes | Run read-only SQL queries against SQLite databases |
| `kitchen_status` | No | Return version health and configuration status |
| `list_skill_scripts` | No | List pipeline scripts from .autoskillit/scripts/ |
| `load_skill_script` | No | Load a pipeline script by name as raw YAML |
| `validate_script` | No | Validate a pipeline script against the workflow schema |

## Skills

Bundled skills invoked as `/autoskillit:<name>`:

| Skill | Purpose |
|-------|---------|
| `investigate` | Deep investigation without code changes |
| `rectify` | Investigation-to-plan bridge |
| `make-plan` | Create implementation plans |
| `make-groups` | Break a large plan into sequenced implementation groups |
| `dry-walkthrough` | Validate plans before implementation |
| `review-approach` | Research modern solutions for a plan |
| `implement-worktree` | Implement in isolated worktree |
| `implement-worktree-no-merge` | Implement without auto-merge (for MCP orchestration) |
| `retry-worktree` | Continue after context exhaustion |
| `resolve-failures` | Fix test failures without merging |
| `mermaid` | Create mermaid diagrams |
| `make-script-skill` | Generate YAML pipeline scripts |
| `setup-project` | Generate tailored pipeline scripts and config for a project |

Use `autoskillit skills list` to see all bundled skills.

## Configuration

Layered YAML resolution: defaults < `~/.autoskillit/config.yaml` (user) < `.autoskillit/config.yaml` (project). Partial configs are fine — unset fields keep defaults. View resolved config with `autoskillit config show`.

### Test Command

Used by `test_check` and `merge_worktree`. The only setting most projects need.

```yaml
test_check:
  command: ["task", "test-all"]
  timeout: 600
```

`autoskillit init` sets this for you. Default: `["pytest", "-v"]`.

### Model Selection

Control which model `run_skill` and `run_skill_retry` use for headless sessions.

```yaml
model:
  default: null      # default model when step has no model field (null = CLI default)
  override: null     # force all sessions to use this model (overrides step YAML)
```

Pipeline script steps can also specify a `model` field per-step in their YAML definition.

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
  default: "haiku"

classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"

reset_workspace:
  command: ["task", "clean"]
  preserve_dirs: ["data", ".cache"]
```

## Workflows

Declarative YAML workflow definitions that guide the orchestrating agent through multi-step processes.

| Workflow | Description |
|----------|-------------|
| `bugfix-loop` | Reset > test > investigate > plan > implement > verify > merge |
| `implementation` | Plan > review > dry-walkthrough > implement |
| `investigate-first` | Investigate > rectify > dry-walkthrough > implement > verify > merge |
| `audit-and-fix` | Audit > investigate > plan > implement |

```bash
autoskillit workflows list              # show available workflows
autoskillit workflows show bugfix-loop  # print YAML
autoskillit update                      # refresh built-ins, preserve customizations
```

Agents access workflows via MCP resource: `workflow://bugfix-loop`. Project workflows in `.autoskillit/workflows/` override built-ins.

## Pipeline Scripts

Pipeline scripts are YAML workflow definitions stored in `.autoskillit/scripts/` that define a complete orchestration loop — which MCP tools and skills to call, in what order, with decision branches at each step.

Scripts use the same YAML schema as workflows (inputs, steps with tool/action, on_success/on_failure routing, retry blocks) with an added `summary` field.

### Example Script

```yaml
name: implementation
description: Plan and implement a task end-to-end.
summary: make-plan > dry-walk > implement > test > merge

inputs:
  task:
    description: What to implement
    required: true
  base_branch:
    description: Branch to merge into
    default: main

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

`/autoskillit:setup-project` generates pipeline scripts tailored to your project.

## Diagnostics

```bash
autoskillit doctor          # check for stale MCP servers, missing config, plugin metadata
autoskillit doctor --json   # structured JSON output
```

## Safety

- **Tool gating**: 10 tools disabled by default, require user activation via MCP prompt
- **Reset guard**: Destructive operations require a marker file (`.autoskillit-workspace`). Create with `autoskillit workspace init <dir>`
- **Dry-walkthrough gate**: Plans must be verified before implementation skills run
- **Test gate**: Programmatic test validation before merge (no bypass parameter)
- **Process tree cleanup**: psutil-based cleanup of all subprocess descendants

## Development

```bash
pip install -e ".[dev]"
pre-commit install
task test-all                        # run tests
pre-commit run --all-files           # format, lint, typecheck
```

## License

MIT
