# agents/

Bundled agent definition markdown files served as MCP resources.

## Files

| File | Purpose |
|------|---------|
| `plan-assumption-challenger.md` | Adversarial agent: verifies implicit assumptions against actual code |
| `plan-completeness-auditor.md` | Adversarial agent: finds entities missed by plan search operations |
| `plan-contract-verifier.md` | Adversarial agent: traces downstream consumers of plan-introduced changes |
| `plan-registry-wire-tracer.md` | Adversarial agent: checks plan-touched files against registry sync patterns |

## Layout

Each `.md` file defines one agent with YAML frontmatter (`name`, `description`,
`tools`, `model`, `maxTurns`) and a markdown body containing the agent's system prompt.

## Agent Packs

| Pack | Tag | Agents | Used By |
|------|-----|--------|---------|
| `plan-review` | `plan-review` | 4 adversarial reviewers | make-plan Steps 6-8 |

## Visibility

Agent resources are hidden at startup via `mcp.disable(tags={pack_tag})`.
Sessions unlock them by calling `unlock_agent_pack(pack_name)`, which calls
`ctx.enable_components(tags={pack_tag})` — per-session, not global.

## Adding Agents

1. Create `{agent-name}.md` in this directory with YAML frontmatter
2. Add the pack to `AGENT_PACK_REGISTRY` in `core/types/_type_constants.py`
3. Add the pack tag to `ALL_VISIBILITY_TAGS` in `core/types/_type_constants.py`
4. Register resource template + index resource in `server/tools/tools_agents.py`
5. Add `mcp.disable(tags={pack_tag})` in `server/__init__.py`