---
name: open-kitchen
uses_capabilities: [open_kitchen]
description: Open the AutoSkillit kitchen — reveals all kitchen MCP tools for this session. Human-only entry point.
disable-model-invocation: true
---

# Open Kitchen

Activate the AutoSkillit kitchen when the host has not already made its tools available.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Call this skill from a headless or automated session (it is human-only)

**ALWAYS:**
- Treat authoritative host/session guidance about pre-revealed tools as the source of truth
- Preserve human-requested activation, named recipe loading, and reopening after `close_kitchen`

## Steps

1. Check the host/session guidance for the current tool state.
2. If the host says the kitchen tools are pre-revealed, confirm that they are already
   active and do not make a redundant no-argument `open_kitchen` call.
3. Otherwise, honor the human-requested activation by calling `open_kitchen` with no
   arguments, then confirm that the kitchen tools are available.

The kitchen state is session-scoped. `open_kitchen(name=...)` remains valid for named
recipe loading, and a no-argument call remains valid to reopen the kitchen after
`close_kitchen`.
