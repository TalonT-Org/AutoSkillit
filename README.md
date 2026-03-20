<p align="center">
  <img src="assets/banner.gif" alt="AutoSkillit — Skill-driven automation for agentic workflows" width="830">
</p>

Automate your processes with skills, automate your skills with Autoskillit.

https://github.com/user-attachments/assets/d1ca806c-e511-499d-8e5f-25c3891a029d

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — package manager (`pip install uv` or see uv docs)
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** — `npm install -g @anthropic-ai/claude-code`
- **[gh CLI](https://cli.github.com/)** — required for GitHub features (PR creation, issue management, CI status)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh | sh
```

## Quick Start

```bash
# 1. Set up your project
cd your-project
autoskillit init

# 2. Run the implementation recipe
autoskillit order implementation
```

### First-Time Project Setup

After installation, run the setup wizard to configure AutoSkillit for your project:

```bash
autoskillit cook
# Then in the Claude session:
/autoskillit:setup-project
```

This explores your codebase and generates project-tailored recipes and config.

That's it. Describe what you want to build, and AutoSkillit handles the rest:

```
Plan ─── Dry-walkthrough ─── Implement ─── Test ─── Merge ─── Push ─── PR ─── Review
 │                                │
 ▼                                ▼
Deep codebase              Isolated worktree
analysis
```

## Bundled Recipes

| Recipe | What it automates |
|--------|-------------------|
| `implementation` | Plan → dry-walkthrough → implement → test → merge → PR → review |
| `remediation` | Investigate → plan → implement → test → merge → PR |
| `implementation-groups` | Decompose large docs → sequenced group implementation |
| `merge-prs` | Analyze open PRs → merge in order → single integration PR |

## CLI

| Command | Purpose |
|---------|---------|
| `autoskillit install` | Register plugin with Claude Code |
| `autoskillit init` | Create project config |
| `autoskillit order [recipe]` | Run a recipe (prompts if omitted) |
| `autoskillit cook` | Launch Claude with all bundled skills as slash commands |

See the [CLI Reference](docs/cli-reference.md) for all commands.

## Documentation

- **[Getting Started](docs/getting-started.md)** — Walk through the implementation workflow step by step
- **[Installation](docs/installation.md)** — Prerequisites, install, troubleshooting
- **[Recipes](docs/recipes.md)** — All recipes with flow diagrams and input reference
- **[CLI Reference](docs/cli-reference.md)** — All commands and options
- **[Configuration](docs/configuration.md)** — Layered config, all settings, examples
- **[Architecture](docs/architecture.md)** — Gating, clone isolation, headless sessions, hooks
- **[Contributing](docs/developer/contributing.md)** — Development setup, testing, architecture layers

## License

MIT
