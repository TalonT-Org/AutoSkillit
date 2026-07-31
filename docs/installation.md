# Installation

## Prerequisites

### Required
- **Python 3.11+** — AutoSkillit uses modern Python features (StrEnum, TaskGroup, ExceptionGroup)
- **Claude Code** — The CLI tool from Anthropic ([install guide](https://docs.anthropic.com/en/docs/claude-code/overview))

### Recommended
- **uv** — Fast Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **[gh CLI](https://cli.github.com/)** — Required for GitHub features (PR creation, issue management, CI status). Install: `brew install gh` or see [gh docs](https://cli.github.com/). Authenticate: `gh auth login`
- **Task** (go-task) — If your project uses Taskfile.yml for test commands

## Quick install

The install script runs five steps:

    curl -fsSL https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh | sh

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

Doctor runs 41 ungated checks: 22 numbered base checks (1–23, excluding 5),
7 lettered sub-checks (`2b`, `2c`, `2d`, `2e`, `4b`, `7b`, `7c`), and
12 backend/runtime checks (30–40, including `31b`). Enabling the fleet feature
adds checks 24–29, for 47 total.
Enumerated by `run_doctor` in `src/autoskillit/cli/doctor/__init__.py`:

| # | Check | What it verifies |
|---|-------|------------------|
| 1 | Stale MCP servers | No dead binaries or nonexistent paths in `~/.claude.json` |
| 2 | MCP server registered | AutoSkillit MCP server is registered (direct entry or via plugin) |
| 2b | Dual MCP registration | No duplicate direct + marketplace registration |
| 2c | Plugin cache exists | `~/.claude/plugins/cache/autoskillit-local/` directory exists |
| 2d | Plugin cache integrity | Cached `hooks.json` paths resolve to real files |
| 2e | Install state consistency | Exact install artifacts, registry entries, retired shapes, and derived versions agree |
| 3 | `autoskillit` on PATH | The CLI command is reachable |
| 4 | Config exists | `.autoskillit/config.yaml` is present |
| 4b | Config secrets placement | Secrets live in `.autoskillit/.secrets.yaml`, never in `config.yaml` |
| 6 | Hook executability | Deployed hook scripts exist and are executable for every event type |
| 7 | Hook registration | Hooks are registered in `settings.json` |
| 7b | Hook registry drift | Structural diff against `generate_hooks_json()` from `hook_registry.py` |
| 7c | Dual hook registration | Plugin-active installs do not also register hooks in `settings.json` |
| 8 | Script version health | Project recipes carry the current `autoskillit_version` |
| 9 | gitignore completeness | `.gitignore` covers `.autoskillit/temp/` and other generated paths |
| 10 | Secret scanning hook | `gitleaks` (or equivalent) is installed as a pre-commit hook |
| 11 | Editable install source exists | An editable install still points at a real source directory |
| 12 | No stale entry points | No leftover `autoskillit` scripts outside `~/.local/bin` |
| 13 | Source version drift | Installed commit SHA vs. branch HEAD (network, with cache fallback) |
| 14 | Quota cache schema | `~/.claude/autoskillit_quota_cache.json` schema version is current |
| 15 | Claude process state | Reports D-state and CPU breakdown of running `claude` processes via `ps` |
| 16 | Install classification | `direct_url.json` classifies the install type and requested revision |
| 17 | Update dismissal state | Active update-prompt dismissal window and conditions, if any |
| 18 | Ambient SESSION_TYPE=leaf | No stray `SESSION_TYPE=leaf` env var in interactive shell |
| 19 | Ambient SESSION_TYPE=orchestrator | No stray `SESSION_TYPE=orchestrator` env var |
| 20 | Ambient SESSION_TYPE=fleet | No stray `SESSION_TYPE=fleet` env var |
| 21 | Ambient CAMPAIGN_ID | No stray `CAMPAIGN_ID` env var in interactive shell |
| 22 | Feature dependency consistency | Enabled features satisfy their declared dependencies |
| 23 | Feature registry import consistency | All feature gate modules import without errors |
| 24–28 | Fleet infrastructure | Sous-chef skill, dispatch guard, stale state, onboarding, clone collisions (fleet feature only) |
| 29 | Fleet state schema | Fleet state schema version drift (fleet feature only) |
| 30 | Codex version | Codex CLI version meets minimum requirement |
| 31 | script(1) binary | PTY binary availability with -qefc support |
| 31b | Claude binary | Claude CLI availability for capability-driven rerouting |
| 32 | MCP timeouts | Codex MCP tool_timeout_sec coherence |
| 33 | Codex graduation | Multi-criteria graduation readiness (version, probe, matrix, smoke) |
| 34 | CLI conformance | Backend CLI accepts minimal TOML config probe |
| 35 | Codex NDJSON drift | Codex event vocabulary matches the supported parser contract |
| 36 | Codex model aliases | Configured Codex model aliases are current |
| 37 | Standing backend pins | Standing backend model pins are feasible |
| 38 | Local recipe validity | Local recipes satisfy the current recipe contract |
| 39 | Codex limits pin | Codex limits version pin is current |
| 40 | Skill capability authenticity | Bundled skill capabilities match authentic source evidence |

See **[Hooks](safety/hooks.md)** for what each PreToolUse / PostToolUse /
SessionStart hook actually enforces.

## Updating

AutoSkillit checks for updates on every interactive invocation and shows a single
consolidated `[Y/n]` prompt when updates are available.  For details on how update
checks work, dismissal windows, and escape hatches, see **[Update Checks](update-checks.md)**.

To update immediately without waiting for the prompt:

    autoskillit update

## Troubleshooting

### "autoskillit: command not found"

If you installed via `uv tool install`, ensure `~/.local/bin` is on your PATH:

    export PATH="$HOME/.local/bin:$PATH"

### "claude: command not found"

Install Claude Code following [Anthropic's guide](https://docs.anthropic.com/en/docs/claude-code/overview).
Then re-run `autoskillit install`.

### Doctor reports an `install_state:*` finding

The shared `install_state_consistency` diagnostic names the exact artifact or
invariant whose installed state disagrees with the registry, a retired shape, or
the running package version. Rebuild and reconcile the install, then verify it:

    autoskillit install
    autoskillit doctor

### Doctor reports "hook_health: ERROR"

Hook scripts are missing. This usually means the package was updated but `install`
wasn't re-run:

    autoskillit install

### MCP server not loading

Check that `~/.claude.json` contains the `autoskillit` entry:

    autoskillit config show

If missing, run `autoskillit init` in your project directory.

### Upgrading

The recommended upgrade path:

    autoskillit update

This fetches the latest version and runs `autoskillit install` automatically.

For manual upgrades (fallback):

    uv tool install --force "git+https://github.com/TalonT-Org/AutoSkillit.git@stable"
    autoskillit install
