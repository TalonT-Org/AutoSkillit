# Architecture

How AutoSkillit runs a recipe end to end: orchestrator, kitchen gating, clone and worktree isolation, session model, and diagnostics.

## Overview

AutoSkillit is a Claude Code plugin that orchestrates automated workflows using headless sessions. It provides 46 MCP tools and 113 bundled skills, organized into a gated visibility system.

## Core Concepts

### Recipes
YAML pipeline definitions that describe a sequence of steps. Each step invokes an MCP tool or a skill. Recipes define the flow; skills do the work.

### Skills
Markdown instruction files (`SKILL.md`) that define what a headless Claude session should do. Skills are registered as `/autoskillit:*` slash commands. Each skill runs in its own context window, so pipelines can run for hours without hitting context limits.

### The Orchestrator
When you run `autoskillit order`, Claude Code acts as a pipeline orchestrator. It reads the recipe, collects ingredients from you, and executes steps in sequence. The orchestrator never reads or writes code itself — it delegates all work through `run_skill` (headless sessions) and `run_cmd` (shell commands).

## Tool Visibility (Kitchen Gating)

AutoSkillit uses a three-tier tool visibility model:

- **Free-range (3 tools)**: Always visible — `open_kitchen`, `close_kitchen`, `disable_quota_guard`
- **Headless tools (1 tool)**: Revealed in headless sessions via `mcp.enable({'headless'})` — `test_check`
- **Kitchen-tagged tools (42 tools total)**: Gated behind `open_kitchen` — `run_skill`,
  `run_cmd`, `run_python`, `merge_worktree`, `clone_repo`, `push_to_remote`, and 31 more.
  One kitchen tool (`test_check`) also carries the `headless` tag and is additionally
  pre-enabled in headless sessions.

When you call `open_kitchen` (automatically done by `order`), all 42 kitchen-tagged tools become
available for that session. This keeps normal Claude Code sessions clean — no pipeline tools
cluttering the tool list.

Functional category subsets (`github`, `ci`, `clone`, `telemetry`) can be disabled in config;
those tools remain hidden even after `open_kitchen`.
See **[MCP Tool Access Control](tool-access.md)** for the complete tool map.

## Clone Isolation

All pipeline work happens in a cloned copy of your repository:

1. `clone_repo` creates a full clone at `../autoskillit-runs/<run>-<timestamp>/`
2. Your working tree and uncommitted changes are never touched
3. The clone's `origin` remote is rewritten to prevent Claude Code from confusing the clone with your real project
4. After the pipeline, you choose whether to keep or delete the clone

## Worktree Isolation

Within the clone, implementation happens in git worktrees:

1. `implement-worktree-no-merge` creates a worktree branched from the feature branch
2. Code changes are committed phase by phase inside the worktree
3. `merge_worktree` rebases the worktree onto the target branch, runs tests, and merges
4. The worktree is cleaned up after a successful merge

## Session Model

AutoSkillit supports four session modes with different tool and skill visibility:

- **`$ claude` (plugin, no kitchen)**: Regular Claude Code session with the AutoSkillit plugin
  loaded. Sees 3 Free Range MCP tools (`open_kitchen`, `close_kitchen`, `disable_quota_guard`) and Tier 1 skills only
  (`open-kitchen`, `close-kitchen`). After calling `/open-kitchen`, all 42 kitchen-tagged MCP
  tools become available.

- **`$ autoskillit cook`**: Interactive development session. Sees all three skill tiers
  (Tier 1+2+3) via an ephemeral session directory. MCP tools are initially ungated (same as
  `$ claude`); `/open-kitchen` reveals kitchen tools.

- **`$ autoskillit order`**: Pipeline orchestrator session. Kitchen is pre-opened at startup —
  all 46 MCP tools are available immediately. All skill tiers are accessible. The orchestrator
  delegates work through `run_skill` (headless sessions) and `run_cmd` (shell commands).

- **`run_skill` (headless)**: Worker sessions launched by the orchestrator. Sees 3 Free Range
  tools + `test_check` (headless-tagged). Cannot call `run_skill`, `run_cmd`, or `run_python`
  — enforced by hooks and code guards. Has access to all native Claude Code tools (Read, Write,
  Bash, etc.) and all skill tiers via `--add-dir skills_extended/`.

This prevents recursive session nesting and keeps the orchestrator as a pure routing engine.
See **[Skill Visibility](../skills/visibility.md)** for the full tier breakdown and configuration.

## Safety

See **[Hooks](../safety/hooks.md)** for the complete safety system: protected branches, quota management, format validation, and session boundary enforcement.

## Session Diagnostics

Pipeline sessions are logged to `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Each session records token usage, timing, and process traces.

Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`

See **[Session Diagnostics](../developer/diagnostics.md)** for details.
