# Architecture

How AutoSkillit works under the hood.

## Overview

AutoSkillit is a Claude Code plugin that orchestrates automated workflows using headless sessions. It provides 39 MCP tools and 60 bundled skills, organized into a gated visibility system.

## Core Concepts

### Recipes
YAML pipeline definitions that describe a sequence of steps. Each step invokes an MCP tool or a skill. Recipes define the flow; skills do the work.

### Skills
Markdown instruction files (`SKILL.md`) that define what a headless Claude session should do. Skills are registered as `/autoskillit:*` slash commands. Each skill runs in its own context window, so pipelines can run for hours without hitting context limits.

### The Orchestrator
When you run `autoskillit cook`, Claude Code acts as a pipeline orchestrator. It reads the recipe, collects ingredients from you, and executes steps in sequence. The orchestrator never reads or writes code itself — it delegates all work through `run_skill` (headless sessions) and `run_cmd` (shell commands).

## Tool Visibility (Kitchen Gating)

AutoSkillit uses a three-tier tool visibility model:

- **Free-range (2 tools)**: Always visible — `open_kitchen`, `close_kitchen`
- **Headless tools (1 tool)**: Revealed in headless sessions via `mcp.enable({'headless'})` — `test_check`
- **Kitchen tools (36 tools)**: Gated behind `open_kitchen` — `run_skill`, `run_cmd`, `run_python`, `merge_worktree`, `clone_repo`, `push_to_remote`, and 30 more

When you call `open_kitchen` (automatically done by `cook`), all 37 kitchen-tagged tools become available for that session. This keeps normal Claude Code sessions clean — no pipeline tools cluttering the tool list.

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

## Two-Tier Session Model

- **Tier 1 (Orchestrator)**: The human-facing session. Has access to all tools. Calls `run_skill` to delegate work.
- **Tier 2 (Worker)**: Headless sessions launched by `run_skill`. Has access to native Claude Code tools (Read, Write, Bash, etc.) plus a subset of ungated MCP tools. Cannot call `run_skill`, `run_cmd`, or `run_python` — enforced by hooks.

This prevents recursive session nesting and keeps the orchestrator as a pure routing engine.

## Safety

See **[Hooks & Safety](hooks-and-safety.md)** for the complete safety system: protected branches, quota management, format validation, and session boundary enforcement.

## Session Diagnostics

Pipeline sessions are logged to `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Each session records token usage, timing, and process traces.

Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`

See **[Session Diagnostics](developer/session-diagnostics.md)** for details.
