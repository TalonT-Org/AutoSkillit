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
- Preserve explicit human-requested activation or promotion, named recipe loading, and
  reopening after `close_kitchen`

## Steps

1. Check the host/session guidance and the user's requested outcome.
2. If the host says the kitchen tools are pre-revealed and the user did not explicitly
   request promotion, confirm that they are already active and do not make a redundant
   no-argument `open_kitchen` call solely to gain access.
3. If the user explicitly requests activation or promotion, call `open_kitchen` with no
   arguments; promotion remains valid even when the tools are pre-revealed.
4. Confirm that the requested kitchen state is active.

The kitchen state is session-scoped. `open_kitchen(name=...)` remains valid for named
recipe loading, and a no-argument call remains valid to reopen the kitchen after
`close_kitchen`.
