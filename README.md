# AutoSkillit

Claude Code plugin that orchestrates automated workflows using headless sessions. Provides 10 MCP tools for running commands, executing skills, testing, merging worktrees, classifying fixes, and discovering pipeline scripts — 8 gated behind user-only MCP prompts, 2 ungated for script discovery. Skills are registered as first-class slash commands (`/autoskillit:investigate`, etc.).

## Install

```bash
pip install -e .
```

Requires Python 3.11+.

## Quick Start

### 1. Install and load as a plugin

```bash
pip install -e /path/to/autoskillit
claude --plugin-dir $(python -c "import autoskillit; print(autoskillit.__path__[0])")
```

The `--plugin-dir` flag loads the plugin, which registers both skills (as `/autoskillit:*` slash commands) and MCP tools (via the bundled `.mcp.json`).

### 2. Configure for your project

```bash
cd your-project
autoskillit init                              # prompts for test command
autoskillit init --test-command "pytest -v"   # non-interactive
```

This creates `.autoskillit/config.yaml` and prints the plugin directory path. Use `--force` to overwrite an existing config.

### 3. Enable tools in session

All tools are disabled by default. Activate them by typing:

```
/mcp__autoskillit__enable_tools
```

This uses MCP prompts (user-only, model cannot invoke) and survives `--dangerously-skip-permissions`.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `run_cmd` | Execute shell commands with timeout |
| `run_skill` | Run Claude Code headless with a skill command |
| `run_skill_retry` | Run Claude Code headless with API call limit (for long-running skills) |
| `test_check` | Run test suite, returns unambiguous PASS/FAIL |
| `merge_worktree` | Merge worktree branch after programmatic test gate |
| `reset_test_dir` | Clear test directory (reset guard marker) |
| `classify_fix` | Analyze diff to determine restart scope (full vs partial) |
| `reset_workspace` | Reset workspace directory, preserving configured paths |
| `list_skill_scripts` | List pipeline scripts from .autoskillit/scripts/ (ungated) |
| `load_skill_script` | Load a pipeline script by name as raw YAML (ungated) |

## Configuration

Layered YAML resolution: defaults < `~/.autoskillit/config.yaml` (user) < `.autoskillit/config.yaml` (project). Partial configs are fine — unset fields keep defaults.

### Required: Test Command

The only setting most projects need. Used by `test_check` and `merge_worktree`'s test gate.

```yaml
test_check:
  command: ["task", "test-all"]   # your project's test command as a list
  timeout: 600                    # seconds before killing (default: 600)
```

`autoskillit init` sets this for you. The default is `["pytest", "-v"]`.

### Optional: classify_fix

Tells `classify_fix` which file paths are critical. When a worktree diff touches files matching these prefixes, the tool returns `full_restart` (re-plan needed). Otherwise it returns `partial_restart` (just re-run implementation).

```yaml
classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"
    - "src/core/config/"
```

Default: `[]` (empty — all changes return `partial_restart`). Only configure this if you use the `bugfix-loop` workflow or call `classify_fix` directly.

### Optional: reset_workspace

Configures the `reset_workspace` tool, which runs a reset command and then clears directory contents (preserving specified directories). Useful for test automation loops that need to reset a scratch directory between iterations.

```yaml
reset_workspace:
  command: ["task", "clean"]                # null = tool disabled
  preserve_dirs: ["data", ".cache"]         # dirs to keep during cleanup
```

Default: `command: null` (disabled), `preserve_dirs: []` (empty). The tool returns an error if called without a configured command.

### Optional: Safety and Gates

```yaml
implement_gate:
  marker: "Dry-walkthrough verified = TRUE"                                            # required first line in plan files
  skill_names: ["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]  # skills subject to gate

safety:
  reset_guard_marker: ".autoskillit-workspace"  # marker file required for reset operations
  require_dry_walkthrough: true                 # plans must be dry-walked before implementation
  test_gate_on_merge: true                      # merge_worktree runs test suite before merging
```

These defaults are usually fine. Override per-project if needed.

### Full Example

A Python web service with schema/migration critical paths and Taskfile-based workspace reset:

```yaml
test_check:
  command: ["pytest", "-v", "--tb=short"]

classify_fix:
  path_prefixes:
    - "src/schema/"
    - "db/migrations/"
    - "tests/integration/"

reset_workspace:
  command: ["task", "clean"]
  preserve_dirs: ["data", ".cache"]
```

### Resolution Order

Defaults < user (`~/.autoskillit/config.yaml`) < project (`.autoskillit/config.yaml`). Project overrides user, user overrides defaults. View resolved config:

```bash
autoskillit config show
```

## Skills

Skills bundled with the plugin, invoked as `/autoskillit:<name>`:

| Skill | Purpose |
|-------|---------|
| `investigate` | Deep investigation without code changes |
| `rectify` | Investigation-to-plan bridge |
| `make-plan` | Create implementation plans |
| `dry-walkthrough` | Validate plans before implementation |
| `review-approach` | Research modern solutions for a plan |
| `implement-worktree` | Implement in isolated worktree |
| `implement-worktree-no-merge` | Implement without auto-merge (for MCP orchestration) |
| `retry-worktree` | Continue after context exhaustion |
| `assess-and-merge` | Fix test failures and merge |
| `mermaid` | Create mermaid diagrams |
| `make-script-skill` | Generate YAML pipeline scripts from workflow descriptions |
| `setup-project` | Explore a project and generate tailored pipeline scripts and config |

Skills are discovered by Claude Code via the plugin structure. Use `autoskillit skills list` to see bundled skills.

## Workflows

Declarative YAML workflow definitions guide the orchestrating agent through multi-step processes.

**Built-in workflows:**

| Workflow | Description |
|----------|-------------|
| `bugfix-loop` | Reset > test > investigate > plan > implement > verify > merge |
| `implementation` | Plan > review > dry-walkthrough > implement |
| `investigate-first` | Investigate > rectify > dry-walkthrough > implement > verify > merge |
| `audit-and-fix` | Audit > investigate > plan > implement |

```bash
autoskillit workflows list          # show available workflows
autoskillit workflows show bugfix-loop  # print YAML
autoskillit update                  # refresh built-ins, preserve customizations
```

Agents access workflows via MCP resource: `workflow://bugfix-loop`

Project workflows in `.autoskillit/workflows/` override built-ins.

## Pipeline Scripts

Pipeline scripts are YAML workflow definitions stored in `.autoskillit/scripts/` that give an orchestrating agent a complete loop to follow — which MCP tools and skills to call, in what order, with decision branches at each step. Agents discover scripts via the `list_skill_scripts` MCP tool and load them via `load_skill_script`.

Scripts use the same YAML schema as workflows (name, description, inputs, steps with tool/action, on_success/on_failure routing, retry blocks) with an added `summary` field for concise pipeline descriptions.

### Discovery and Loading

```
list_skill_scripts()          → JSON array of {name, description, summary}
load_skill_script("impl")    → raw YAML content for agent to interpret
```

Both tools are ungated — available without calling `enable_tools`.

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

`/autoskillit:setup-project` generates pipeline scripts tailored to your project. Run `/autoskillit:setup-project /path/to/your-project` to get started.

## Diagnostics

```bash
autoskillit doctor          # check for stale MCP servers, missing config, plugin metadata
autoskillit doctor --json   # structured JSON output
```

Checks: dead MCP server binaries, plugin metadata, `autoskillit` command on PATH, missing project config.

## Safety

- **Tool gating**: All tools disabled by default, require user activation via MCP prompt
- **Reset guard**: Destructive operations require a marker file (`.autoskillit-workspace`) in the target directory. Create with `autoskillit workspace init <dir>`
- **Dry-walkthrough gate**: Plans must be verified before implementation skills run
- **Test gate**: Programmatic test validation before merge (no bypass parameter)
- **Process tree cleanup**: psutil-based cleanup of all subprocess descendants

## Development

```bash
pip install -e ".[dev]"
pre-commit install

pytest -v                        # run tests
pre-commit run --all-files       # format, lint, typecheck
```

## Project Structure

```
src/autoskillit/
  .claude-plugin/      Plugin metadata (plugin.json)
  .mcp.json            MCP server configuration for the plugin
  cli.py               Cyclopts CLI (serve, init, config, skills, workflows, update, doctor)
  config.py            Dataclass config + layered YAML loading
  script_loader.py     Pipeline script discovery from .autoskillit/scripts/
  server.py            FastMCP server with 10 tools + 2 prompts + resources
  process_lifecycle.py  Subprocess management (temp I/O, tree cleanup, timeouts)
  skill_resolver.py    Bundled skill listing
  workflow_loader.py   Workflow YAML parsing + validation
  skills/              12 bundled skills (utilities and building blocks)
  workflows/           4 built-in workflow definitions
```

## License

MIT
