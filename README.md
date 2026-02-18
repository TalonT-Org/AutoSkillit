# AutoSkillit

MCP server that orchestrates automated workflows with Claude Code headless sessions. Provides 8 tools for running commands, executing skills, testing, merging worktrees, and classifying fixes — all gated behind user-only MCP prompts.

## Install

```bash
# From cloned repo
pip install -e .

# With interactive init wizard (questionary-based prompts)
pip install -e ".[wizard]"
```

Requires Python 3.11+.

## Quick Start

### 1. Install and register with Claude Code

```bash
pip install -e /path/to/autoskillit
claude mcp add autoskillit -- autoskillit
```

The `claude mcp add` command registers the server so Claude Code can discover it. Scope options:

```bash
# Default (local) — just you, just this project
claude mcp add autoskillit -- autoskillit

# Project — writes .mcp.json, shared with team via git
claude mcp add --scope project autoskillit -- autoskillit

# User — available across all your projects
claude mcp add --scope user autoskillit -- autoskillit
```

### 2. Configure for your project

```bash
cd your-project
autoskillit init                              # interactive wizard
autoskillit init --quick                      # just test command + defaults
autoskillit init --test-command "pytest -v"   # fully non-interactive
```

This creates `.autoskillit/config.yaml` with project-specific settings. Use `--force` to overwrite an existing config.

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
| `reset_test_dir` | Clear test directory (playground safety guard) |
| `classify_fix` | Analyze diff to determine restart scope (full vs partial) |
| `reset_workspace` | Reset workspace directory, preserving configured paths |

## Configuration

Layered YAML resolution: defaults < `~/.autoskillit/config.yaml` (user) < `.autoskillit/config.yaml` (project). Partial configs are fine — unset fields keep defaults.

```yaml
test_check:
  command: ["pytest", "-v"]
  timeout: 600

classify_fix:
  path_prefixes: ["src/core/"]

reset_workspace:
  command: ["make", "clean"]
  preserve_dirs: [".cache", "reports"]

implement_gate:
  marker: "Dry-walkthrough verified = TRUE"
  skill_names: ["/implement-worktree", "/implement-worktree-no-merge"]

safety:
  playground_guard: true
  require_dry_walkthrough: true
  test_gate_on_merge: true

skills:
  resolution_order: ["project", "user", "bundled"]
```

View resolved config: `autoskillit config show`

## Skills

10 pipeline skills are bundled with the package:

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

### Resolution Order

Skills are resolved by name using a first-match-wins hierarchy. The default order:

1. **project** — `.claude/skills/<name>/SKILL.md` in the current repo
2. **user** — `~/.claude/skills/<name>/SKILL.md` in your home directory
3. **bundled** — skills shipped inside the autoskillit package

If the same skill name exists at multiple levels, the highest-priority source wins. For example, a project-level `rectify` skill shadows the bundled one.

The order is configurable in `.autoskillit/config.yaml`:

```yaml
skills:
  resolution_order: ["project", "user", "bundled"]  # default
```

Use `autoskillit skills list` to see which source won for each skill.

Skills are also exposed as `skill://` MCP resources for protocol-level discovery and reading.

```bash
autoskillit skills list              # show all with sources
autoskillit skills install investigate  # copy bundled to project
```

## Workflows

Declarative YAML workflow definitions guide the orchestrating agent through multi-step processes.

**Built-in workflows:**

| Workflow | Description |
|----------|-------------|
| `bugfix-loop` | Reset > test > investigate > plan > implement > verify > merge |
| `implementation` | Plan > review > dry-walkthrough > implement |
| `audit-and-fix` | Audit > investigate > plan > implement |

```bash
autoskillit workflows list          # show available workflows
autoskillit workflows show bugfix-loop  # print YAML
autoskillit update                  # refresh built-ins, preserve customizations
```

Agents access workflows via MCP resource: `workflow://bugfix-loop`

Project workflows in `.autoskillit/workflows/` override built-ins.

## Safety

- **Tool gating**: All tools disabled by default, require user activation via MCP prompt
- **Playground guard**: Destructive operations require "playground" in path
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
  cli.py               Cyclopts CLI (serve, init, config, skills, workflows, update)
  config.py            Dataclass config + layered YAML loading
  server.py            FastMCP server with 8 tools + 2 prompts + resources
  process_lifecycle.py  Subprocess management (temp I/O, tree cleanup, timeouts)
  skill_resolver.py    Skill resolution hierarchy
  workflow_loader.py   Workflow YAML parsing + validation
  skills/              10 bundled pipeline skills
  workflows/           3 built-in workflow definitions
```

## License

MIT
