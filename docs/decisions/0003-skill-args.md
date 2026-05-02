# ADR-0003: Pass Skill Inputs as Arguments, Not Environment Variables

**Status:** Accepted
**Date:** 2026-05-02
**Issue:** [#1625](https://github.com/TalonT-Org/AutoSkillit/issues/1625)

## Context

- Recipe steps can declare an `env:` key inside `with:` which is stored in `with_args["env"]` by the YAML parser.
- The `run_skill` MCP tool has no `env` parameter and `build_leaf_headless_cmd` inherits only `os.environ`.
- Environment variables declared in recipe `env:` blocks are never propagated to headless subprocesses.
- The planner recipe's `inputs.task` and `inputs.task_file` were delivered via `env:` to `planner-generate-phases` and `planner-extract-domain`, meaning those skills never received their task input.

## Decision

> **All skill inputs go through positional arguments in `skill_command`. Environment variables MUST NOT be used to deliver skill inputs.**

A semantic rule (`env-key-in-with-args`) rejects any step that contains an `env:` key in `with_args` at recipe validation time.

## Rationale

Positional arguments in `skill_command` flow through the entire pipeline:

1. Recipe YAML (`skill_command: "/autoskillit:skill-name $1 $2 $3"`)
2. Orchestrator LLM (reads and forwards the command string)
3. `run_skill` MCP call (passes `skill_command` as-is)
4. `build_leaf_headless_cmd` (embeds in `--skill-command`)
5. Headless session (receives `$1`, `$2`, `$3` as positional args)
6. `SKILL.md` (documents and reads `$N` references)

Environment variables have no delivery path from recipe YAML to the headless subprocess. The `env:` key is silently stored and ignored, creating a failure mode that is invisible at recipe load time.

## Consequences

- Existing `env:` blocks in recipe steps must be migrated to positional args in `skill_command`.
- SKILL.md files must document `$N` positional args instead of `ENV_VAR` references.
- The semantic rule catches future regressions at validation time.
