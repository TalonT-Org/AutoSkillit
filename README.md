# automation-mcp

MCP server that orchestrates automated workflows with Claude Code headless sessions. Provides 8 tools for running commands, executing skills, testing, merging worktrees, and classifying fixes — all gated behind user-only MCP prompts.

## Install

```bash
pip install -e .

# Optional: interactive init wizard
pip install -e ".[wizard]"
```

Requires Python 3.11+.

## Quick Start

### 1. Initialize a project

```bash
cd your-project
automation-mcp init          # interactive wizard
automation-mcp init --quick  # just test command + defaults
```

This creates `.automation-mcp/config.yaml`.

### 2. Add to your MCP client config

```json
{
  "mcpServers": {
    "bugfix-loop": {
      "command": "automation-mcp"
    }
  }
}
```

### 3. Enable tools in session

All tools are disabled by default. Activate them by typing:

```
/mcp__bugfix-loop__enable_tools
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
| `classify_fix` | Analyze diff to determine restart scope (plan vs executor) |
| `reset_executor` | Reset executor state, preserving plans and agent data |

## Configuration

Layered YAML resolution: defaults < `~/.automation-mcp/config.yaml` (user) < `.automation-mcp/config.yaml` (project). Partial configs are fine — unset fields keep defaults.

```yaml
test_check:
  command: ["pytest", "-v"]
  timeout: 600

classify_fix:
  path_prefixes: ["src/planner/"]

reset_executor:
  command: ["ai-executor", "reset-status"]
  preserve_dirs: [".agent_data", "plans"]

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

View resolved config: `automation-mcp config show`

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

Resolution hierarchy: project (`.claude/skills/`) > user (`~/.claude/skills/`) > bundled. Project skills shadow bundled ones with the same name.

```bash
automation-mcp skills list              # show all with sources
automation-mcp skills install investigate  # copy bundled to project
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
automation-mcp workflows list          # show available workflows
automation-mcp workflows show bugfix-loop  # print YAML
automation-mcp update                  # refresh built-ins, preserve customizations
```

Agents access workflows via MCP resource: `workflow://bugfix-loop`

Project workflows in `.automation-mcp/workflows/` override built-ins.

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
src/automation_mcp/
  cli.py               Cyclopts CLI (serve, init, config, skills, workflows, update)
  config.py            Dataclass config + layered YAML loading
  server.py            FastMCP server with 8 tools + 2 prompts + 1 resource
  process_lifecycle.py  Subprocess management (temp I/O, tree cleanup, timeouts)
  skill_resolver.py    Skill resolution hierarchy
  workflow_loader.py   Workflow YAML parsing + validation
  skills/              10 bundled pipeline skills
  workflows/           3 built-in workflow definitions
```

## License

MIT
