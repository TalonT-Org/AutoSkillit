---
name: reload-session
description: Reload the current AutoSkillit session — signals the parent process to re-launch with the full wrapper environment and resume the conversation.
disable-model-invocation: true
---

# Reload Session

Use this skill when the kitchen is disconnected, context is exhausted, or a fresh re-launch
is needed without losing the conversation transcript.

## Steps

1. Call `reload_session` with no arguments.
2. Run `/exit` immediately after to allow the parent process to detect the reload request.

The parent autoskillit process will re-launch claude with `--resume <session_id>` and
the original orchestrator environment (system prompt, tools, session type env vars) fully
restored.

## Critical Constraints

**NEVER:**
- Skip running `/exit` after `reload_session` — the reload does not happen until claude exits
- Call this skill from a headless or automated session

**ALWAYS:**
- Call `reload_session` first, then immediately run `/exit`
