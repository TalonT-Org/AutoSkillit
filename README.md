<p align="center">
  <img src="assets/banner.gif" alt="AutoSkillit — Skill-driven automation for agentic workflows" width="830">
</p>

Give it a task, get back a tested PR.

<!-- demo recording here (owner will add) -->

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — package manager (`pip install uv` or see uv docs)
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** — `npm install -g @anthropic-ai/claude-code`
- **[gh CLI](https://cli.github.com/)** — required for GitHub features (PR creation, issue management, CI status)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh | sh
```

Or manually: `uv tool install "git+https://github.com/TalonT-Org/AutoSkillit.git@stable" && autoskillit install`

## Quick Start

```bash
# 1. Set up your project
cd your-project
autoskillit init

# 2. Run the implementation pipeline
autoskillit cook implementation
```

### First-Time Project Setup

After installation, run the setup wizard to configure AutoSkillit for your project:

```bash
autoskillit cook setup-project
```

This generates project-tailored recipes and config by exploring your codebase.

That's it. Describe what you want to build, and AutoSkillit handles the rest:

```
Plan ─── Verify ─── Implement ─── Test ─── Merge ─── Push ─── PR ─── Review
 │                     │            │                          │
 │                     ▼            ▼                          ▼
 │               Dry-walkthrough  Worktree               7 parallel
 │               validates plan   isolation              audit bots
 ▼
Deep codebase
analysis with
arch diagrams
```

## What Happens

When you run `autoskillit cook implementation`:

1. **You describe the task** — or paste a GitHub issue URL
2. **AutoSkillit clones your repo** into an isolated directory (your working tree is never touched)
3. **A plan is created** — deep codebase analysis, architecture diagrams, test-first design
4. **The plan is verified** — a dry walkthrough catches gaps before any code is written
5. **Code is implemented** in a git worktree, committed, and tested
6. **If tests fail**, a fix skill diagnoses and resolves failures automatically
7. **Changes are merged, pushed**, and a PR is opened
8. **The PR is reviewed** by 7 parallel audit subagents checking architecture, tests, bugs, defense, cohesion, slop, and deletion regressions
9. **CI is monitored** — if it fails, AutoSkillit diagnoses and fixes

The orchestrator never reads or writes code itself. Every step runs in a separate
headless session with its own context window, so pipelines can run for hours without
hitting context limits.

## Key Features

**Zero Footprint by Default** — AutoSkillit exposes 38 MCP tools, but only 12 lightweight tools are visible in a normal Claude Code session. The other 26 gated pipeline tools stay hidden until you explicitly open the kitchen. This means AutoSkillit never pollutes your context window or wastes tokens on tool descriptions you aren't using. When you need the full pipeline, one command reveals everything.

**Clone Isolation** — All pipeline work happens in a cloned copy of your repo. Your working tree and uncommitted changes are never touched.

**Dry-Walkthrough Gate** — Every plan is validated against the actual codebase before implementation begins. Missing files, wrong function signatures, and broken assumptions are caught and fixed in the plan — not discovered during implementation.

**7-Dimension PR Review** — The `review-pr` skill runs 7 parallel audit subagents: architecture layering, test quality, defensive coding, bug patterns, cohesion, AI slop detection, and deletion regression checks. Each posts inline GitHub comments on the PR.

**Contract Cards** — Static analysis of recipe dataflow. Each skill declares its inputs and outputs; contract cards verify that every step has the data it needs before the pipeline runs.

## Bundled Recipes

| Recipe | What it automates |
|--------|-------------------|
| `implementation` | Plan → verify → implement → test → merge → PR → review |
| `bugfix-loop` | Test → investigate → plan → implement → verify → merge |
| `remediation` | Investigate → plan → verify → implement → test → merge → PR |
| `audit-and-fix` | Audit → investigate → plan → implement → test → merge → PR |
| `implementation-groups` | Decompose large docs → sequenced group implementation |
| `batch-implementation` | Implement multiple GitHub issues sharing clone setup overhead |
| `pr-merge-pipeline` | Analyze open PRs → merge in order → single integration PR |
| `smoke-test` | Integration self-test of the orchestration engine |

## CLI Quick Reference

| Command | Purpose |
|---------|---------|
| `autoskillit install` | Register plugin with Claude Code |
| `autoskillit init` | Create project config |
| `autoskillit cook [recipe]` | Launch a pipeline |
| `autoskillit doctor` | Check setup health |
| `autoskillit chefs-hat` | Launch Claude with all bundled skills as slash commands |
| `autoskillit migrate [--check]` | Report and apply recipe format migrations |
| `autoskillit upgrade` | Migrate project from legacy scripts/ format |
| `autoskillit quota-status` | Check Anthropic quota utilization |
| `autoskillit config show` | Display resolved configuration |
| `autoskillit recipes list/show/render` | Recipe management |
| `autoskillit skills list` | List bundled skills |
| `autoskillit workspace init/clean` | Workspace management |

## Documentation

- **[Installation](docs/installation.md)** — Prerequisites, manual install, troubleshooting
- **[Getting Started](docs/getting-started.md)** — Full tutorial with the implementation recipe
- **[Recipes](docs/recipes.md)** — All recipes with flow diagrams and input reference
- **[Architecture](docs/architecture.md)** — Gating, clone isolation, headless sessions, hooks
- **[CLI Reference](docs/cli-reference.md)** — All commands and options
- **[Configuration](docs/configuration.md)** — Layered config, all settings, examples

## License

MIT
