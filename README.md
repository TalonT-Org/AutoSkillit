# AutoSkillit

A stateless workflow engine that turns your skills into scriptable components. Write simple YAML recipes that chain skills as reusable steps.

Skills are focused tasks (planning, implementing, testing, investigating). Recipes are instructions that tell the AI how to chain them together. The YAML format is a convention for consistency and sharing, but there's nothing strict about it. Anything you could tell a person to do, you can put in a recipe. The only limit is whether the AI can understand what you want.

<!-- TODO: banner -->

<!-- TODO: demo -->

## Prerequisites

- Python 3.11+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and on PATH

## Quick Start

### 1. Install

```bash
git clone https://github.com/talontechnologies/autoskillit.git
cd autoskillit
uv pip install -e .
autoskillit install
```

`install` registers AutoSkillit as a Claude Code plugin. It loads automatically in every session after this. No per-project wiring needed.

### 2. Set up your project

```bash
cd your-project
autoskillit init
```

This creates `.autoskillit/config.yaml` with your test command, the only setting most projects need. For a guided setup that detects your tools and generates tailored recipes, use `/autoskillit:setup-project` inside Claude Code.

### 3. Run your first pipeline

```bash
autoskillit cook
```

This launches Claude Code with the kitchen already open. Select a recipe, provide the inputs, and the orchestrator handles the rest. You can also specify a recipe directly:

```bash
autoskillit cook implementation
```

## How It Works

AutoSkillit is a **stateless workflow engine**. The recipe defines the script. The AI is the state manager.

A **skill** is a focused task: "make a plan", "implement in a worktree", "investigate test failures". Each skill runs in its own headless session with full tool access. On its own, a skill is a one-shot capability.

A **recipe** turns skills into a script. It tells the AI what steps to run, what inputs to collect, and what to do when something succeeds or fails. The YAML format is a convention, not a constraint. The AI interprets the recipe and decides how to execute it.

When you run `autoskillit cook`, an orchestrating agent reads the recipe and drives the workflow. It never does the actual work itself. It just passes inputs to each skill, reads the result, and moves to the next step. All the real work (reading code, writing code, running tests) happens inside separate headless skill sessions, each with their own context window.

This means the orchestrator's context window stays small. It only ever holds the recipe, the current step's result, and enough routing information to decide what comes next. Workflows have run for 48+ hours without approaching context limits, because the orchestrator never accumulates the content of the skills it delegates to.

### Example: The Implementation Pipeline

The bundled `implementation` recipe automates the full development cycle:

```
clone > plan > verify > implement > test > merge > push
```

Give it a task description or a GitHub issue URL, and it:

- Clones your repo into an isolated directory
- Creates a detailed implementation plan
- Validates the plan with a dry walkthrough
- Implements changes in a git worktree
- Runs your test suite
- Merges on success, pushes, and opens a PR

If tests fail, it automatically routes to a fix skill that diagnoses and resolves the failures before retrying.

## Bundled Recipes

| Recipe | What it automates |
|--------|-------------------|
| `implementation` | Plan, verify, implement, test, merge, and push |
| `bugfix-loop` | Test, investigate, plan, implement, verify, and merge |
| `remediation` | Investigate-first approach for issues needing diagnosis |
| `audit-and-fix` | Audit, investigate, rectify, implement, test, and merge |
| `smoke-test` | Integration self-test of the orchestration path |

```bash
autoskillit recipes list              # list available recipes
autoskillit recipes show bugfix-loop  # inspect a recipe's YAML
```

Project recipes in `.autoskillit/recipes/` override bundled ones with the same name. Generate custom recipes with `/autoskillit:write-recipe` or `/autoskillit:setup-project`.

## CLI Reference

| Command | Purpose |
|---------|---------|
| `autoskillit install` | Register plugin with Claude Code |
| `autoskillit init` | Create project config (`.autoskillit/config.yaml`) |
| `autoskillit cook [recipe]` | Launch a pipeline |
| `autoskillit doctor` | Check setup for common issues |
| `autoskillit recipes list` | List available recipes |
| `autoskillit skills list` | List all 24 bundled skills |
| `autoskillit config show` | Show resolved configuration |
| `autoskillit migrate` | Check for outdated recipes |

## Configuration

`autoskillit init` writes a `.autoskillit/config.yaml` with your test command:

```yaml
test_check:
  command: ["pytest", "-v"]
```

Config resolves in layers: package defaults < user config (`~/.autoskillit/config.yaml`) < project config (`.autoskillit/config.yaml`). View the result with `autoskillit config show`.

See [docs/configuration.md](docs/configuration.md) for the full reference covering model selection, worktree setup, quota guard, GitHub integration, and safety settings.

## Safety

AutoSkillit is designed around defense in depth:

- **Tool gating**: 16 tools are locked until you run the `/open_kitchen` command
- **Test gate**: code must pass tests before merge, no bypass
- **Dry-walkthrough gate**: plans are verified before implementation begins
- **Clone isolation**: pipeline work happens in cloned directories, not your working tree
- **Process cleanup**: all subprocess trees are cleaned up after sessions

## License

MIT
