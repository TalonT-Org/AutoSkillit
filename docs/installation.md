# Installation

## Prerequisites

### Required
- **Python 3.11+** — AutoSkillit uses modern Python features (StrEnum, TaskGroup, ExceptionGroup)
- **Claude Code** — The CLI tool from Anthropic ([install guide](https://docs.anthropic.com/en/docs/claude-code/overview))

### Recommended
- **uv** — Fast Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Task** (go-task) — If your project uses Taskfile.yml for test commands

## Quick Install

The install script handles everything:

    curl -fsSL https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh | sh

What it does:
1. Checks for Python 3.11+ (installs via brew/apt if missing)
2. Checks for uv (installs if missing)
3. Checks for Claude Code (fails with install link if missing)
4. Installs AutoSkillit from the `stable` branch via `uv tool install`
5. Registers the plugin with Claude Code via `autoskillit install`

## Manual Install

### Option A: uv tool from stable branch (recommended)

    uv tool install "git+https://github.com/TalonT-Org/AutoSkillit.git@stable"
    autoskillit install

### Option B: pip from stable branch (into an existing venv)

    pip install "git+https://github.com/TalonT-Org/AutoSkillit.git@stable"
    autoskillit install

### Option C: Development install (from main branch)

    git clone https://github.com/TalonT-Org/AutoSkillit.git
    cd AutoSkillit
    uv pip install -e '.[dev]'
    autoskillit install

> **Note:** End users should install from the `stable` branch. The `main` branch
> is for active development and may contain unreleased changes.

## What `autoskillit install` Does

1. Creates a local plugin marketplace at `~/.autoskillit/marketplace/`
2. Symlinks the installed package into the marketplace
3. Registers the marketplace with Claude Code: `claude plugin marketplace add`
4. Installs the plugin: `claude plugin install autoskillit@autoskillit-local`
5. Syncs hook scripts into Claude Code's `settings.json`

After this, AutoSkillit loads automatically in every Claude Code session.

## Project Setup

    cd your-project
    autoskillit init

This creates `.autoskillit/config.yaml` with your test command. The only setting most
projects need.

## Post-Install Verification

    autoskillit doctor

Doctor runs 8 checks:
| Check | What it verifies |
|-------|-----------------|
| stale_mcp_servers | No dead MCP entries in ~/.claude.json |
| mcp_server_registered | AutoSkillit MCP server is registered |
| autoskillit_on_path | `autoskillit` command is available |
| project_config | `.autoskillit/config.yaml` exists |
| version_consistency | Installed package matches plugin.json |
| hook_health | All hook scripts exist on disk |
| hook_registration | Hooks are registered in settings.json |
| script_version_health | Project recipes are up to date |

## Troubleshooting

### "autoskillit: command not found"

If you installed via `uv tool install`, ensure `~/.local/bin` is on your PATH:

    export PATH="$HOME/.local/bin:$PATH"

### "claude: command not found"

Install Claude Code following [Anthropic's guide](https://docs.anthropic.com/en/docs/claude-code/overview).
Then re-run `autoskillit install`.

### Doctor reports "version_consistency: WARNING"

Your installed package version doesn't match the plugin manifest. Re-run:

    autoskillit install

### Doctor reports "hook_health: ERROR"

Hook scripts are missing. This usually means the package was updated but `install`
wasn't re-run:

    autoskillit install

### MCP server not loading

Check that `~/.claude.json` contains the `autoskillit` entry:

    autoskillit config show

If missing, run `autoskillit init` in your project directory.

### Upgrading

    uv tool install --force "git+https://github.com/TalonT-Org/AutoSkillit.git@stable"
    autoskillit install

Always run `autoskillit install` after upgrading to sync the plugin cache and hooks.
