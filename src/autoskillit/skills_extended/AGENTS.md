# skills_extended/

Extended catalog of bundled skills covering the arch-lens, exp-lens,
vis-lens, audit, planner, and diagnostic workflows. `reload-session` carries
`disable-model-invocation: true` and is not slash-command-invocable (it is a
recovery action invoked via its MCP tool); the other extended skills are
slash-command-invocable as `/autoskillit:<skill-name>`. The canonical catalog
lives in `docs/skills/catalog.md`.

This is the bulk skill catalog. The always-loaded kernel skills
(`open-kitchen`, `sous-chef`, `close-kitchen`) live one directory up in
`skills/` and are documented in `skills/AGENTS.md`. The catalog test verifies
that every extended skill directory is represented.

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

## Literal write targets

Every write target in a `bash` fence, prose-prescribed shell, or Write-tool
instruction must be a literal path. Obtain dynamic values once with a read-only
command and paste the printed values through `{placeholder}`s. For files in
shared temp storage, generate an invocation token with a timestamp and UUID:

```bash
python -c 'from datetime import datetime; from uuid import uuid4; print(datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid4().hex)'
```

Substitute that value for `{run_id}` in every later path. Never write through
shell variables, command substitutions, backticks, or `~`. Read arguments may
use variables assigned literal paths within the same Bash call; variables do
not persist across tool calls. Redirect, `tee`, `cp`, `mv`, `rm`, `sed -i`, and
`install` targets must repeat the literal path.

`{name}` is the only placeholder syntax; `<name>` parses as a redirect. Label
shell command fences `bash` so they are included in the guard corpus.
`tests/contracts/test_skill_write_target_conformance.py` checks Bash fences and
bundled recipe `run_cmd` commands against the installed write-target guards.
A later prose conformance part extends enforcement to prose-prescribed shell.
Keep this guidance here; do not add code comments that merely restate it.
