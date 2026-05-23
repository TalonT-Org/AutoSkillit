# agents/

Bundled agent definition markdown files that serve as both **plugin agents**
(discovered natively by Claude Code at session start) and **MCP resources**
(available via `unlock_agent_pack`).

## Files

| File | Purpose |
|------|---------|
| `plan-foundation-auditor.md` | Adversarial agent: control-flow auditor — traces branch scope, return placement, guard coverage |
| `plan-interface-mapper.md` | Adversarial agent: variable/data-flow tracer — builds SET/READ tables for wrong-variable detection |
| `plan-registry-tracer.md` | Adversarial agent: registry/artifact auditor — LSP + tree-sitter + grep symbol tracing |

## Layout

Each `.md` file defines one agent with YAML frontmatter (`name`, `description`,
`tools`, `model`, `maxTurns`) and a markdown body containing the agent's system prompt.

## Invocation

### Native `subagent_type` (preferred)

Agent definitions in this directory are discovered at session start by Claude Code's
plugin agent system. They are registered as `autoskillit:{agent-name}` in the
subagent registry and invoked via:

```
Agent(subagent_type="autoskillit:{agent-name}", prompt="...")
```

Claude Code automatically applies tool restrictions, model, maxTurns, and the
markdown body as the system prompt from the agent definition frontmatter.

**Used by:** make-plan Steps 6-9, rectify Steps 5-7

### MCP Resource path (available for programmatic access)

Agent resources are hidden at startup via `mcp.disable(tags={pack_tag})`.
Sessions unlock them by calling `unlock_agent_pack(pack_name)`, which calls
`ctx.enable_components(tags={pack_tag})` — per-session, not global. Once unlocked,
agent definitions are readable via `ReadMcpResourceTool` at `agent://{pack}/{name}`.

## Agent Packs

| Pack | Tag | Agents | Used By |
|------|-----|--------|---------|
| `plan-review` | `plan-review` | 3 adversarial reviewers | make-plan Steps 6-9, rectify Steps 5-7 |

## Adding Agents

1. Create `{agent-name}.md` in this directory with YAML frontmatter
2. Add the pack to `AGENT_PACK_REGISTRY` in `core/types/_type_constants.py`
3. Add the pack tag to `ALL_VISIBILITY_TAGS` in `core/types/_type_constants.py`
4. Register resource template + index resource in `server/tools/tools_agents.py`
5. Add `mcp.disable(tags={pack_tag})` in `server/__init__.py`