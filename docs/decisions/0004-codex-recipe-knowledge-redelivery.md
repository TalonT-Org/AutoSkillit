# ADR-0004: Codex Recipe Knowledge Re-Delivery via load_recipe

**Status:** Accepted
**Date:** 2026-06-27
**Issue:** [#4051](https://github.com/TalonT-Org/AutoSkillit/issues/4051)

## Context

Codex auto-compaction fires at 90% of the 258K context window. When triggered, it can
destroy recipe content delivered by `open_kitchen` in the initial orchestrator response.
After compaction, the agent loses the recipe steps it needs to complete the pipeline.

Two defenses exist:

1. **Primary — disable compaction**: `ensure_codex_mcp_registered` writes
   `model_auto_compact_token_limit = 999_999_999` (an unreachable threshold) to
   `~/.codex/config.toml`. `setup_session_dir` copies this config into each session
   directory, ensuring every headless session inherits the setting.

2. **Guard — block raw recipe reads**: The `recipe_read_guard.py` PreToolUse hook
   blocks `run_cmd`/`Bash` from reading recipe YAML, SKILL.md, or agent definition
   files, and blocks `run_python` from calling `autoskillit.recipe.*` callables.
   This prevents the agent from self-recovering from compaction loss via raw file
   access — compaction loss is treated as a hard failure, not a recoverable state.

If the primary defense is ever relaxed (e.g., Codex fixes its compaction behavior or
the context window grows), the agent needs a sanctioned channel to re-acquire recipe
knowledge without reading raw files.

## Decision

> **`load_recipe` (the MCP tool exposed by `server/tools/tools_recipe.py`) is the
> sanctioned channel for recipe knowledge re-delivery after context compaction.**

If recipe content is lost, the agent must call `load_recipe` to re-acquire it. The
`recipe_read_guard` enforces that only this path is allowed — raw file access is denied.

This is a forward obligation: when the primary defense (unreachable auto-compact limit)
is relaxed, `load_recipe` re-delivery must be tested end-to-end as the recovery path.

## Consequences

- `recipe_read_guard.py` error messages direct the agent to call `load_recipe`.
- No production code changes are needed today — the channel already exists and works.
- Future work relaxing `CODEX_AUTO_COMPACT_LIMIT` must add integration tests verifying
  `load_recipe` re-delivery restores full pipeline execution capability.
- `test_copied_config_has_auto_compact_limit` validates the primary defense path:
  `setup_session_dir` preserves the override in the copied `config.toml`.