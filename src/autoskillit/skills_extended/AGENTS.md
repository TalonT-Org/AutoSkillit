# skills_extended/

Extended catalog of bundled skills covering the arch-lens, exp-lens,
vis-lens, audit, planner, and diagnostic workflows. Of the 138 bundled
skills, only `reload-session` carries `disable-model-invocation: true`
and is NOT slash-command-invocable (it is a recovery action invoked via
its MCP tool, not a slash command); the remaining 137 are slash-command
invocable as `/autoskillit:<skill-name>`. The canonical accounting lives
in `docs/skills/catalog.md`.

This is the bulk skill catalog. The always-loaded kernel skills
(`open-kitchen`, `sous-chef`, `close-kitchen`) live one directory up in
`skills/` and are documented in `skills/AGENTS.md`. Authoritative
counts are pinned by `tests/docs/test_doc_counts.py` and
`_count_skills_total`.

`skills_extended/` is a Markdown-only skill-catalog directory (no
Python package, so it is not assigned to any import-linter IL layer).

## Architecture Notes

Skills here cover interactive orchestrator workflows (most) and
headless-orchestrator workflows (a few, e.g. `codex-impl-loop`,
`smoke-task`). Skills are grouped for discovery via the `categories:`
frontmatter field; canonical categories include `arch-lens`,
`exp-lens`, `vis-lens`, `audit`, `planner`, `recipe`, `fleet`, `ci`,
`diagnostic`, and `research`.

### Materialization path

At session start, `workspace/session_skills/` copies a relevant subset
of these skills into the active workspace so they can be invoked as
slash commands (`/autoskillit:<skill-name>`). The full set is never
copied — the session-skill catalog filters by relevance to the
orchestrator mode. See `workspace/AGENTS.md` (the canonical
materialization contract) and `workspace/session_skills/` (the
implementation) for details.

### Frontmatter fields

All frontmatter fields are optional; coverage is uneven across the
catalog (see issue #3773 F-C1-11 for the known gaps, currently
out-of-scope for this plan):

- `categories:` — discovery grouping; one or more of the canonical
  categories above. Recommended for slash-command-invocable skills
  (i.e. everything except `reload-session`, which is invoked via its MCP
  tool, not a slash command).
- `hooks:` — PreToolUse/PostToolUse announcements emitted when the
  skill runs. Pure observability; absence means the skill runs
  silently.
- `uses_capabilities:` — declared MCP capabilities (`run_skill`,
  `test_check`, `open_kitchen`, `github_api_write`, etc.). Drives
  capability-gated dispatch and audit trails.

## Multi-concern files

Files with no registration support their folder; one immediate-child
subdirectory per skill. The authoritative enumeration is the directory
listing itself (catalog pointer in the intro above). For the canonical
visibility matrix see `docs/skills/visibility.md`.
