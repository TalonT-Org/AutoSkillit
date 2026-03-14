# CLI Reference

## autoskillit install

Register AutoSkillit as a Claude Code plugin.

    autoskillit install [--scope user|project|local]

**Flags:**
- `--scope` (default: `user`) — Where to install: `user` (global), `project` (per-project), `local`

**What it does:**
1. Creates local marketplace at `~/.autoskillit/marketplace/`
2. Registers marketplace with Claude Code
3. Installs the plugin
4. Syncs hooks to `settings.json`

Run after every upgrade.

---

## autoskillit init

Set up a project for AutoSkillit.

    autoskillit init [--force] [--test-command CMD] [--scope user|project]

**Flags:**
- `--force` — Overwrite existing config
- `--test-command` — Set test command non-interactively (e.g., `--test-command "pytest -v"`)
- `--scope` (default: `user`) — Where to register hooks

**Creates:**
- `.autoskillit/config.yaml` — Project configuration
- `temp/` — Working directory for pipeline artifacts
- MCP server entry in `~/.claude.json`

---

## autoskillit cook

Launch an interactive pipeline session.

    autoskillit cook [recipe]

**Arguments:**
- `recipe` (optional) — Recipe name. If omitted, shows a selection menu.

**Behavior:**
- Validates the recipe YAML before launching
- Opens a restricted Claude Code session (only `AskUserQuestion` + MCP tools)
- Injects the recipe as the orchestrator's system prompt
- Cannot be run from inside a Claude Code session

**Examples:**

    autoskillit cook                    # Show recipe menu
    autoskillit cook implementation     # Run implementation pipeline

---

## autoskillit doctor

Run health checks on your setup.

    autoskillit doctor [--output-json] [--fix]

**Flags:**
- `--output-json` — Output results as JSON
- `--fix` — Attempt to fix issues automatically

Runs 8 checks: stale MCP servers, MCP registration, PATH, project config,
version consistency, hook health, hook registration, recipe version health.

---

## autoskillit chefs-hat

Launch Claude Code with all skills as slash commands.

    autoskillit chefs-hat

Alias: `autoskillit chef`

This gives you an unrestricted Claude session with all 36 bundled skills
available as `/autoskillit:*` slash commands and the kitchen pre-opened.
No recipe — use skills individually as needed.

---

## autoskillit migrate

Check for outdated project recipes.

    autoskillit migrate [--check]

**Flags:**
- `--check` — Exit with code 1 if any recipes need migration (for CI)

Migrations are applied automatically when recipes are loaded. This command
just reports what's pending.

---

## autoskillit quota-status

Check current API quota utilization.

    autoskillit quota-status

Outputs JSON with the current 5-hour rolling utilization percentage.

---

## autoskillit config show

Show the resolved configuration.

    autoskillit config show

Prints the merged result of all config layers as JSON.

---

## autoskillit recipes list

List available recipes.

    autoskillit recipes list

Shows name, source (bundled or project), and description.

---

## autoskillit recipes show

Print a recipe's raw YAML.

    autoskillit recipes show <name>

---

## autoskillit recipes render

Generate flow diagrams for recipes.

    autoskillit recipes render [name]

If no name given, renders all recipes. Diagrams are written to
`recipes/diagrams/{name}.md`.

---

## autoskillit skills list

List all bundled skills.

    autoskillit skills list

Shows name, source, and path for each of the 36 bundled skills.

---

## autoskillit workspace init

Create a prep station directory for testing.

    autoskillit workspace init <path>

Creates the directory with a `.autoskillit-workspace` marker that authorizes
`reset_test_dir` and `reset_workspace` to clear it.

---

## autoskillit workspace clean

Prune old run directories.

    autoskillit workspace clean [--dir DIR] [--force]

**Flags:**
- `--dir` — Directory to clean (default: `../autoskillit-runs/`)
- `--force` — Skip confirmation prompt

Removes run directories older than 5 hours.
